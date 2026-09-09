"""Date-aware Rule 3/26 scope screening using recorded, confirmed facts.

Printed phrases suggest a scope question; they cannot prove the transaction,
buyer or category. Revised findings identify the policy's rule-pack version.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from ..domain.enums import DeclarationClass, PackageClass
from ..domain.models import Declaration, PackageFacts

_DATE_REVIEW = (
    "Manufacture/packing event evidence is uncertain or conflicting. Confirm dated "
    "applicability before applying a Rule 3/26 exclusion; the capture date does not "
    "resolve the printed-date conflict.")


@dataclass
class Determination:
    package_class: PackageClass
    exemptions: list[str]
    considered: list[str]
    in_scope: bool
    review_reasons: list[str] = field(default_factory=list)
    source_text: str = ""
    # Facts that must withhold a favourable Rule 3/26 exemption without making
    # every screened duty undecidable. `review_reasons` is a global channel --
    # `legal.scope` turns any entry into INCONCLUSIVE for every rule -- so a
    # concern that only bears on granting relief belongs here instead.
    exemption_blockers: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.review_reasons:
            return "Applicability requires review: " + " ".join(self.review_reasons)
        if self.exemption_blockers:
            return "No exemption established: " + " ".join(self.exemption_blockers)
        if not self.in_scope:
            return f"Confirmed {self.package_class.value} transaction: Chapter II retail-package duties excluded under Rule 3."
        if self.exemptions:
            return "Exempt from screened PCR duties under " + "; ".join(self.exemptions) + "."
        return "Retail-package screening; no exemption established from the available facts."


def _policy() -> dict:
    directory = Path(__file__).resolve().parents[3] / "rules" / "lmpcr-2011"
    if not directory.is_dir():
        directory = Path(sys.prefix) / "share" / "tula" / "rules" / "lmpcr-2011"
    return json.loads((directory / "pack.json").read_text(encoding="utf-8")).get("legal_policy", {})


def determine(
    declarations: dict[DeclarationClass, Declaration], *, raw_text: str = "",
    declared_class: PackageClass | None = None, legal_context: dict | None = None,
    as_of: date | None = None, policy: dict | None = None,
) -> Determination:
    if policy == {}:
        return _legacy_determine(declarations, raw_text=raw_text, declared_class=declared_class)
    legal = legal_context or {}
    rules = (_policy() if policy is None else policy).get("exemptions", {})
    when = as_of
    if when is None and legal.get("assessment_date_confirmed") is True:
        try:
            when = date.fromisoformat(legal["assessment_date"])
        except (KeyError, TypeError, ValueError):
            pass
    when = when or datetime.now(UTC).date()
    texts = [d.raw for d in declarations.values()] + [raw_text, legal.get("scope_evidence_text", "")]
    blob = "\n".join(dict.fromkeys(line.strip().lower() for text in texts
                                  for line in text.splitlines() if line.strip()))
    result = Determination(PackageClass.RETAIL, [], ["PCR Rule 3 and Rule 26 applicability"], True)
    result.source_text = blob
    # A self-contradictory printed date must not buy the package a favourable
    # exemption, but it is not a reason to abandon every duty: whether a net
    # quantity is declared at all does not depend on which of two candidate
    # dates is right. Rules whose applicability genuinely turns on a date are
    # gated individually by `legal.scope`, which tests the rule's own
    # transition. This used to be a review reason, which made every rule on
    # every date-ambiguous package INCONCLUSIVE.
    date_declaration = declarations.get(DeclarationClass.DATE_OF_PACKING)
    if date_declaration and date_declaration.norm.get("date_evidence_uncertain"):
        result.exemption_blockers.append(_DATE_REVIEW)

    category = legal.get("category", "unknown")
    confirmed = legal.get("category_confirmed") is True and category != "unknown"
    scope_confirmed = legal.get("exemption_confirmed") is True
    actual_class = legal.get("package_class") or (declared_class.value if declared_class else None)
    if actual_class in ("institutional", "industrial"):
        if scope_confirmed:
            result.package_class = PackageClass(actual_class)
            result.in_scope = False
            return result
        result.review_reasons.append("Confirm the purchaser, supply channel and intended use before excluding institutional/industrial packages under Rule 3.")
    elif any(term in blob for term in rules.get("institutional_terms", [])):
        result.review_reasons.append("A non-retail/institutional phrase was detected. It does not establish the purchaser, supply channel or actual use required for a Rule 3 exclusion.")

    net = declarations.get(DeclarationClass.NET_QUANTITY)
    value = net.norm.get("value_base") if net and not net.norm.get("requires_review") else None
    unit = net.norm.get("unit_base") if net else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        value = None
    if value is not None and unit in ("g", "ml") and value <= rules.get("small_limit", 10):
        tobacco = category == "tobacco" or any(t in blob for t in rules.get("tobacco_terms", []))
        pan_masala = category == "pan_masala" or "pan masala" in blob
        tobacco_from = date.fromisoformat(rules.get("tobacco_from", "2016-01-01"))
        pan_from = date.fromisoformat(rules.get("pan_masala_from", "2026-02-01"))
        if (tobacco and when >= tobacco_from) or (pan_masala and when >= pan_from):
            result.considered.append("Rule 26(a) small-pack relief withheld under the effective tobacco/pan-masala proviso.")
        elif confirmed:
            result.exemptions.append(f"Rule 26(a): confirmed category, net quantity {value:g} {unit} does not exceed {rules.get('small_limit', 10):g} {unit}")
        else:
            result.review_reasons.append("Small quantity was detected; confirm product category and dated tobacco/pan-masala exceptions before applying Rule 26(a).")

    if value is not None and unit in ("g", "ml") and value > rules.get("ordinary_large_limit", 25000):
        if when < date.fromisoformat(rules.get("large_from", "2018-01-01")):
            result.review_reasons.append("Historical large-pack applicability requires the earlier Rule 3 text and transaction context.")
        elif scope_confirmed and legal.get("large_package_exclusion") is True:
            result.exemptions.append("Rule 3(a): officer-confirmed quantity/category exclusion from Chapter II")
        else:
            result.review_reasons.append("Verify the Rule 3 quantity exclusion and cement, fertilizer or agricultural-produce exceptions; a large net quantity alone does not settle applicability.")
    if "fast food" in blob and ("restaurant" in blob or "hotel" in blob):
        if scope_confirmed and legal.get("restaurant_fast_food") is True:
            result.exemptions.append("Rule 26(b): confirmed fast food packed by a restaurant or hotel")
        else:
            result.review_reasons.append("Verify that this is fast food actually packed by a restaurant or hotel before applying Rule 26(b).")
    if category in ("drug", "medical_device", "garment"):
        result.considered.append("Category-specific declarations/exceptions require duty-level legal review; no blanket exemption applied.")
    if result.review_reasons or result.exemption_blockers:
        result.exemptions.clear()
    return result


def apply(facts: PackageFacts, determination: Determination) -> PackageFacts:
    legal = dict(facts.legal_context)
    legal["legal_review_reasons"] = list(determination.review_reasons)
    legal["exemption_blockers"] = list(determination.exemption_blockers)
    # The commodity category, exported *only* once it is actually settled, so a
    # rule can ask about it three-valued with a single comparison.
    #
    # `category` on its own cannot do that job: an officer who opens the package
    # facts form and saves it without choosing stores the string "unknown", and
    # a rule comparing that against "food" gets a definite False -- so the rule
    # silently drops out of the record instead of telling the officer that
    # confirming the category is what would decide it. Absent here means
    # unsettled, which resolves to None, which is the undecided answer the
    # engine and the console's next-step panel both already understand.
    category = legal.get("category", "unknown")
    if legal.get("category_confirmed") is True and category != "unknown":
        legal["confirmed_category"] = category
    else:
        legal.pop("confirmed_category", None)
    if determination.source_text:
        legal["scope_evidence_text"] = determination.source_text
    return facts.model_copy(update={"klass": determination.package_class,
                                   "exemptions": determination.exemptions,
                                   "legal_context": legal})


def _legacy_determine(declarations, *, raw_text="", declared_class=None):
    """Reproduce the archived 2026.09.07-draft filter, not current legal advice.

    Only an explicitly empty archived policy selects this path. Never retrofit
    later law into an old revision while retaining its old rules-version label.
    """
    blob = " ".join([d.raw for d in declarations.values()] + [raw_text]).lower()
    considered = ["Rule 3 (applicability to retail packages)"]
    klass = declared_class or PackageClass.RETAIL
    terms = ("not for retail sale", "for institutional use", "institutional pack",
             "for industrial use", "industrial pack", "for catering purpose")
    if declared_class is None and any(t in blob for t in terms):
        klass = PackageClass.INSTITUTIONAL
    if klass in (PackageClass.INSTITUTIONAL, PackageClass.INDUSTRIAL):
        return Determination(klass, [], considered, False)
    net = declarations.get(DeclarationClass.NET_QUANTITY)
    value, unit = (net.norm.get("value_base"), net.norm.get("unit_base")) if net and not net.norm.get("requires_review") else (None, None)
    value = float(value) if isinstance(value, (int, float)) else None
    tobacco = any(t in blob for t in ("tobacco", "pan masala", "gutkha", "gutka", "cigarette", "bidi", "beedi", "khaini", "zarda", "snuff"))
    relief = []
    considered.append("Rule 26 (packages of 10 g/ml or less)")
    if value is not None and unit in ("g", "ml") and 0 < value <= 10 and not tobacco:
        relief.append(f"Rule 26 (net quantity {value:g} {unit} does not exceed 10 {unit})")
    elif value is not None and value <= 10 and tobacco:
        considered.append("Rule 26 small-package exemption withheld: tobacco products are carved out")
    considered.append("Rule 26 (fast food packed by a restaurant or hotel)")
    if "fast food" in blob and any(t in blob for t in ("packed by a restaurant", "packed by restaurant", "packed by a hotel", "packed by hotel")):
        relief.append("Rule 26 (fast food item packed by a restaurant or hotel)")
    considered.append("Rule 26 (agricultural farm produce above 50 kg)")
    if any(t in blob for t in ("farm produce", "agricultural produce", "raw agricultural")) and value is not None and unit == "g" and value > 50000:
        relief.append("Rule 26 (agricultural farm produce exceeding 50 kg)")
    return Determination(klass, relief, considered, True)
