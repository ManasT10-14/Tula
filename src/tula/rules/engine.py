"""Rule pack orchestration.

Takes the facts a scan produced, resolves which version of the statute was in
force on the packing date, evaluates every applicable rule, and emits findings
that carry enough provenance to survive a challenge.

The tier gate at the bottom is the part worth reading twice: a rule that needs
a physical measurement simply cannot return VIOLATION on evidence weaker than
its `min_assurance_tier`. That is what stops a citizen's phone snap from
manufacturing an accusation, and it is enforced here rather than trusted to
callers.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from ..domain.enums import DeclarationClass, Verdict
from ..domain.models import (
    Analysis,
    Citation,
    Declaration,
    Finding,
    Measured,
    PackageFacts,
    Scan,
    short_hash,
)
from . import exemptions, legal
from .evaluator import EvalContext, evaluate
from .spec import Rule, RulePack, load_pack

DEFAULT_PACK_DIR = Path(__file__).resolve().parents[3] / "rules" / "lmpcr-2011"
if not DEFAULT_PACK_DIR.is_dir():
    DEFAULT_PACK_DIR = Path(sys.prefix) / "share" / "tula" / "rules" / "lmpcr-2011"


class RulesEngine:
    def __init__(self, pack: RulePack):
        self.pack = pack

    @classmethod
    def from_directory(cls, directory: str | Path | None = None) -> RulesEngine:
        return cls(load_pack(Path(directory or DEFAULT_PACK_DIR)))

    # ------------------------------------------------------------------
    # fact assembly
    # ------------------------------------------------------------------

    @staticmethod
    def build_facts(
        scan: Scan,
        package: PackageFacts,
        declarations: dict[DeclarationClass, Declaration],
        measurements: dict[str, Measured],
    ) -> dict:
        """Flatten everything a rule may read into one namespace tree.

        Rules address facts as `$decl.net_quantity.norm.unit_raw`,
        `$pkg.pdp_area_cm2`, `$meas.net_quantity_cap_height`, `$ctx.tier`.
        Nothing else is reachable from a rule pack.
        """

        return {
            "decl": {k.value: v for k, v in declarations.items()},
            "pkg": package,
            "meas": measurements,
            "ctx": {
                "tier": scan.tier.value,
                "lane": scan.lane.value,
                "packing_date": scan.packing_date,
                "captured_at": scan.captured_at,
                "panels": [p.value for p in package.panels_captured],
            },
        }

    # ------------------------------------------------------------------
    # evaluation
    # ------------------------------------------------------------------

    def evaluate_all(
        self,
        scan: Scan,
        package: PackageFacts,
        declarations: dict[DeclarationClass, Declaration],
        measurements: dict[str, Measured],
        *,
        as_of: date | None = None,
    ) -> list[Finding]:
        # Packing and sale/assessment are different events. The supplied legal
        # context records the chosen date; transitions remain explicit review.
        when = as_of
        date_basis = "explicit assessment date" if as_of else ""
        supplied = package.legal_context
        if when is None and supplied.get("assessment_date_confirmed") is True:
            try:
                when = date.fromisoformat(supplied["assessment_date"])
                date_basis = "confirmed assessment date"
            except (KeyError, TypeError, ValueError):
                pass
        if when is None:
            when = scan.packing_date or scan.captured_at.date()
            date_basis = "packing-date fallback" if scan.packing_date else "capture-date fallback"
        if self.pack.legal_policy:
            determination = exemptions.determine(
                declarations, raw_text=package.legal_context.get("scope_evidence_text", ""),
                legal_context=package.legal_context, as_of=when, policy=self.pack.legal_policy,
            )
            package = exemptions.apply(package, determination)
        facts = self.build_facts(scan, package, declarations, measurements)
        facts["ctx"].update(assessment_date=when, date_basis=date_basis)

        findings: list[Finding] = []
        for rule in self.pack.in_force_on(when):
            finding = self._evaluate_one(rule, facts, scan, package)
            if finding is not None:
                findings.append(finding)

        order = {
            Verdict.VIOLATION: 0,
            Verdict.ADVISORY: 1,
            Verdict.INCONCLUSIVE: 2,
            Verdict.UNVERIFIED: 3,
            Verdict.EXEMPT: 4,
            Verdict.PASS: 5,
            Verdict.NOT_APPLICABLE: 6,
        }
        severity_order = {"critical": 0, "major": 1, "minor": 2}
        findings.sort(
            key=lambda f: (order[f.verdict], severity_order[f.severity.value], f.rule_id)
        )
        return findings

    def _evaluate_one(
        self,
        rule: Rule,
        facts: dict,
        scan: Scan,
        package: PackageFacts,
    ) -> Finding | None:
        ctx = EvalContext(
            facts=facts,
            coverage=scan.coverage,
            absence_provable_from=list(rule.absence_provable_from),
        )
        if self.pack.legal_policy:
            def record_measurement(node):
                if isinstance(node, str) and node.startswith("$meas."):
                    measured = ctx.resolve(node)
                    if isinstance(measured, Measured):
                        ctx.trace.setdefault("measured", measured)
                elif isinstance(node, dict):
                    for value in node.values():
                        record_measurement(value)
                elif isinstance(node, list):
                    for value in node:
                        record_measurement(value)
            record_measurement(rule.assertion)
        if package.klass.value != "retail" and not self.pack.legal_policy:
            return None
        declaration = facts.get("decl", {}).get(rule.declaration.value) if rule.declaration else None
        if declaration is not None and declaration.norm.get("requires_review"):
            return self._finding(rule, scan, Verdict.INCONCLUSIVE, ctx,
                                 detail="Competing OCR or spatial interpretations remain unresolved. Verify the source declaration before applying this rule.")

        # Applicability is a different question from compliance, and `present`
        # means something different in each. In `applies_when` it is a guard --
        # "is there a subject to test here at all" -- so a net quantity we never
        # found simply means the unit-symbol rule has nothing to examine, and the
        # rule drops out silently. In `assert` the same operator is a claim about
        # the package, and there coverage decides whether we may make it. Sharing
        # a trace, not a coverage model, keeps the two apart.
        gate = EvalContext(facts=facts, trace=ctx.trace)
        applies = evaluate(gate, rule.applies_when)
        if applies is False:
            return None  # silently out of scope; not a finding
        if applies is None:
            return self._finding(
                rule, scan, Verdict.UNVERIFIED, ctx,
                detail="Could not establish whether this rule applies to the package.",
            )

        if self.pack.legal_policy:
            if package.klass.value != "retail":
                return self._finding(rule, scan, Verdict.EXEMPT, ctx,
                                     detail="Confirmed Rule 3 exclusion from the retail-package duties screened here. Other legal duties are not decided.")
            if (package.exemptions
                    and not package.legal_context.get("legal_review_reasons")
                    and not package.legal_context.get("exemption_blockers")):
                return self._finding(rule, scan, Verdict.EXEMPT, ctx,
                                     detail="Relieved from the screened PCR duty by " + "; ".join(package.exemptions))
            scoped = legal.scope(rule, facts, facts["ctx"]["assessment_date"], self.pack.legal_policy)
            if scoped is not None:
                verdict, detail = scoped
                if verdict is Verdict.INCONCLUSIVE:
                    ctx.trace["legal_review"] = True
                return self._finding(rule, scan, verdict, ctx, detail=detail)

        if package.exemptions:
            return self._finding(
                rule, scan, Verdict.EXEMPT, ctx,
                detail="Relieved by " + "; ".join(package.exemptions),
            )

        result = evaluate(ctx, rule.assertion)

        if result is True:
            verdict = Verdict.PASS
        elif result is None:
            verdict = Verdict.INCONCLUSIVE
        else:
            verdict = Verdict.VIOLATION

        detail = ctx.trace.get("legal_detail", "")
        verdict, detail = legal.qualify_adverse(rule, facts, verdict, detail)
        # ---- gate 1: assurance tier ----
        # A rule may not sustain a violation on evidence weaker than it needs.
        measured = ctx.trace.get("measured")
        evidence_tier = min((measured.tier, scan.tier), key=lambda t: t.rank) if isinstance(measured, Measured) else scan.tier
        if verdict in (Verdict.VIOLATION, Verdict.PASS) and not evidence_tier.satisfies(rule.min_assurance_tier):
            verdict = Verdict.INCONCLUSIVE
            detail = (
                    f"This check requires Tier "
                f"{rule.min_assurance_tier.value} evidence and the scan is Tier "
                f"{evidence_tier.value}. Re-capture with a scale reference before "
                f"this can be recorded as a violation."
            )

        # ---- gate 2: lane ----
        # Independent of how good the photograph is. A citizen submission or a
        # marketplace listing may direct an inspection; neither is evidence in
        # an enforcement proceeding, so neither may convict. The non-conformity
        # is still reported -- as an advisory, which is what it actually is.
        if verdict is Verdict.VIOLATION and scan.lane.advisory_only:
            verdict = Verdict.ADVISORY
            detail = (
                f"{scan.lane.why_advisory} The non-conformity stands on its face "
                "and should be verified by an inspector capture before any notice "
                "is issued."
            )

        return self._finding(rule, scan, verdict, ctx, detail=detail)

    # ------------------------------------------------------------------

    def _finding(
        self,
        rule: Rule,
        scan: Scan,
        verdict: Verdict,
        ctx: EvalContext,
        *,
        detail: str = "",
    ) -> Finding:
        trace = ctx.trace
        measured = trace.get("measured")
        threshold = trace.get("threshold")

        message = {
            Verdict.PASS: rule.messages.passed,
            Verdict.VIOLATION: rule.messages.violation,
            # An advisory is the same non-conformity; only its standing differs.
            Verdict.ADVISORY: rule.messages.violation,
            Verdict.INCONCLUSIVE: rule.messages.inconclusive or rule.messages.violation,
        }.get(verdict, rule.title)
        if not message:
            message = rule.title
        if trace.get("legal_review"):
            message = "Manual legal review required: " + rule.title + "."

        # A rule's inconclusive text falls back to its violation text, which for
        # a presence check reads "no retail sale price is declared on the
        # package" -- an assertion of absence, on a verdict that exists precisely
        # because absence could not be established. Replace it outright rather
        # than trusting every pack author to write a third message.
        if verdict is Verdict.INCONCLUSIVE and trace.get("absence_unprovable"):
            message = (
                f"Whether the package carries this declaration "
                f"({rule.citation.clause}) could not be established from the "
                "evidence captured."
            )
        elif verdict is Verdict.INCONCLUSIVE and trace.get("parse_gap"):
            message = (
                f"The declaration required by {rule.citation.clause} was located "
                "but could not be read well enough to judge."
            )

        if not detail:
            detail = self._explain(trace, verdict)
        penalty_ref = rule.penalty_ref
        when = ctx.facts.get("ctx", {}).get("assessment_date")
        enforcement = self.pack.legal_policy.get("enforcement", {})
        if when and enforcement.get("from") and when >= date.fromisoformat(enforcement["from"]):
            penalty_ref = enforcement["reference"]
        if self.pack.legal_policy and when:
            detail = (detail + f" Assessment date: {when.isoformat()} ({ctx.facts['ctx']['date_basis']}).").strip()

        extracted = None
        if rule.declaration is not None:
            decl = ctx.resolve(f"$decl.{rule.declaration.value}")
            extracted = getattr(decl, "raw", None)

        return Finding(
            finding_id=f"F-{scan.scan_id}-{short_hash(rule.id, 6)}",
            rule_id=rule.id,
            rules_version=self.pack.version,
            citation=Citation(
                act=rule.citation.act,
                rules=rule.citation.rules,
                clause=rule.citation.clause,
                text=rule.citation.text,
            ),
            declaration=rule.declaration,
            verdict=verdict,
            severity=rule.severity,
            plain=rule.plain,
            # The next step is only worth printing while something is still
            # open. On a rule that passed there is nothing to do.
            plain_action=(rule.plain_action
                          if verdict in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED,
                                         Verdict.VIOLATION, Verdict.ADVISORY) else ""),
            message=message,
            detail=detail,
            measured=measured if isinstance(measured, Measured) else None,
            threshold=float(threshold) if isinstance(threshold, (int, float)) else None,
            threshold_basis=trace.get("threshold_basis"),
            extracted=extracted,
            evidence=rule.evidence,
            penalty_ref=penalty_ref,
            tier=measured.tier if isinstance(measured, Measured) else scan.tier,
        )

    @staticmethod
    def _explain(trace: dict, verdict: Verdict) -> str:
        """Turn the evaluation trace into the sentence a respondent reads."""
        bits: list[str] = []
        # Why a presence check came back undecided is the single most useful
        # sentence in the report: it tells the officer what to do next.
        if trace.get("absence_unprovable"):
            bits.append(trace["absence_unprovable"])
        if trace.get("parse_gap"):
            bits.append(trace["parse_gap"])
        measured, threshold = trace.get("measured"), trace.get("threshold")
        if isinstance(measured, Measured) and threshold is not None:
            bits.append(f"Measured {measured.render()} against a limit of {threshold:.2f} mm.")
            if verdict is Verdict.VIOLATION:
                bits.append(
                    f"Upper bound {measured.upper:.2f} mm is below the limit, so the "
                    "non-conformity holds across the whole measurement interval."
                )
            elif verdict is Verdict.INCONCLUSIVE:
                bits.append(
                    f"The interval {measured.lower:.2f}-{measured.upper:.2f} mm straddles "
                    "the limit; the evidence does not decide the question."
                )
        if trace.get("threshold_basis"):
            bits.append(f"Threshold basis: {trace['threshold_basis']}.")
        if trace.get("scripts_note"):
            bits.append(trace["scripts_note"])
        elif trace.get("scripts_missing"):
            bits.append("Scripts missing: " + ", ".join(trace["scripts_missing"]) + ".")
        if trace.get("arithmetic"):
            bits.append(trace["arithmetic"].capitalize() + ".")
        if trace.get("legal_detail"):
            bits.append(trace["legal_detail"])
        return " ".join(bits)


def attach(analysis: Analysis, engine: RulesEngine) -> Analysis:
    """Run the engine over a completed analysis and fill in its findings."""
    analysis.findings = engine.evaluate_all(
        analysis.scan, analysis.package, analysis.declarations, analysis.measurements
    )
    analysis.rules_version = engine.pack.version
    return analysis
