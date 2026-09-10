"""The test bench.

Two things you cannot do by uploading photographs:

  * check a measurement against what was actually printed, and
  * assert that a specific rule reaches a specific verdict for a stated reason.

This module does both. `run_spec` draws a label at known millimetre geometry,
runs the full pipeline over it, and reports the measurement next to the drawn
truth. `SCENARIOS` is an acceptance matrix: each entry isolates one behaviour
and states the verdict it must produce.

Fixture recognition isolates pipeline logic; real OCR tests recognition and
measurement together. A recognized English declaration can satisfy the script
screen under Rule 9(4), without requiring a second language.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from .analyse import AnalyseOptions, Capture, analyse
from .domain.enums import Lane, Panel
from .domain.models import Analysis, Measured
from .labgen import GroundTruth, LabelSpec, render
from .rules.engine import RulesEngine

ABSENT = "ABSENT"  # the rule must not be emitted at all


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    key: str
    title: str
    proves: str
    spec: LabelSpec
    expect: dict[str, str] = field(default_factory=dict)
    # overrides applied when the engine is not the perfect fixture recogniser
    expect_real: dict[str, str] = field(default_factory=dict)
    lane: Lane = Lane.FIELD
    # Ceiling on adverse findings for the whole run. The degraded-evidence
    # scenarios need to assert the absence of accusations, not just the verdict
    # on one clause -- "no violations anywhere" is the actual property.
    max_violations: int | None = None

    def expectations(self, engine_name: str) -> dict[str, str]:
        merged = dict(self.expect)
        if engine_name != "fixture":
            merged.update(self.expect_real)
        return merged


R8_HEIGHT = "LMPCR.R8.2.NETQTY_HEIGHT"
R8_MIN = "LMPCR.R8.1.MIN_HEIGHT"
R_UNIT_SYMBOL = "LMPCR.R6.1.C.UNIT_SYMBOL"
R_MRP_FORM = "LMPCR.R6.1.E.MRP_FORM"
R_MRP_SINGLE = "LMPCR.R6.1.E.MRP_SINGLE"
R_ROUNDING = "LMPCR.R11.MRP_ROUNDING"
R_UNIT_PRICE = "LMPCR.R6.1.F.UNIT_PRICE_PRESENT"
R_UNIT_MATHS = "LMPCR.R6.1.F.UNIT_PRICE_ARITHMETIC"
R_BILINGUAL = "LMPCR.R9.3.BILINGUAL"
R_NET_QTY = "LMPCR.R6.1.C.NET_QUANTITY"
R_GTIN = "FORENSIC.GTIN.PREFIX"
R_GTIN_CHECK = "FORENSIC.GTIN.CHECK_DIGIT"
R_EXPIRY_FOOD = "LMPCR.R6.1.D.EXPIRY_FOOD"
R_MRP_PRESENT = "LMPCR.R6.1.E.MRP_PRESENT"
R_GENERIC = "LMPCR.R6.1.B.GENERIC_NAME"
R_MANUFACTURER = "LMPCR.R6.1.A.MANUFACTURER"
R_CONSUMER_CARE = "LMPCR.R6.1.F.CONSUMER_CARE"

# A GTIN whose GS1 prefix (690) is allocated by China, printed on a pack that
# declares Indian origin.
GTIN_CHINA = "6901234567892"


SCENARIOS: list[Scenario] = [
    Scenario(
        key="compliant",
        title="Reference label",
        proves="The system is not merely a violation printer. Everything correct "
               "must come back clean.",
        spec=LabelSpec(),
        expect={R8_HEIGHT: "PASS", R8_MIN: "INCONCLUSIVE", R_UNIT_SYMBOL: "PASS",
                R_MRP_FORM: "PASS", R_UNIT_MATHS: "PASS", R_BILINGUAL: "PASS",
                R_GTIN: "PASS"},
        expect_real={R_BILINGUAL: "PASS"},
    ),
    Scenario(
        key="undersize_numerals",
        title="Net quantity printed below the Rule 7 minimum",
        proves="Metrology. 1.4 mm numerals on a 216 cm² panel need 2.5 mm, and the "
               "whole measurement interval sits below the limit.",
        spec=LabelSpec(net_qty_mm=1.4),
        expect={R8_HEIGHT: "VIOLATION"},
    ),
    Scenario(
        key="boundary_straddle",
        title="Numerals measuring right at the limit",
        proves="The guard band. At 2.5 mm against a 2.5 mm limit the interval "
               "straddles the threshold, so the system declines to decide.",
        spec=LabelSpec(net_qty_mm=2.5),
        expect={R8_HEIGHT: "INCONCLUSIVE"},
    ),
    Scenario(
        key="citizen_tier_c",
        title="Same undersize print, but Tier C evidence",
        proves="The tier gate. A monocular estimate may raise a flag but can "
               "never sustain a violation, no matter what it measures.",
        spec=LabelSpec(net_qty_mm=1.4, scale_source="mono_metric"),
        expect={R8_HEIGHT: "INCONCLUSIVE"},
    ),
    Scenario(
        key="large_panel",
        title="Adequate print, but on a much larger panel",
        proves="Threshold selection by panel area. 3 mm passes on 216 cm² and "
               "fails on 600 cm², where the table demands 4 mm.",
        spec=LabelSpec(net_qty_mm=3.0, panel_w_mm=200.0, panel_h_mm=300.0),
        expect={R8_HEIGHT: "VIOLATION"},
    ),
    Scenario(
        key="non_standard_unit",
        title='Net quantity declared as "200 gms"',
        proves="Unit-symbol conformance. Prescribed symbols are case-sensitive "
               "and are not pluralised — invisible to presence-detection.",
        spec=LabelSpec(net_qty_unit="gms"),
        expect={R_UNIT_SYMBOL: "VIOLATION", R_NET_QTY: "PASS"},
    ),
    Scenario(
        key="missing_tax_clause",
        title="MRP without “inclusive of all taxes”",
        proves="Prescribed form, not just presence. The price is there; the "
               "statutory phrasing is not.",
        spec=LabelSpec(tax_clause=False),
        expect={R_MRP_FORM: "VIOLATION"},
    ),
    Scenario(
        key="dual_mrp",
        title="Two retail sale prices on one pack",
        proves="Dual pricing is only visible in aggregate — a single-value "
               "parser would just report the last one it saw.",
        spec=LabelSpec(second_mrp=55.0),
        expect={R_MRP_SINGLE: "INCONCLUSIVE"},
    ),
    Scenario(
        key="unrounded_mrp",
        title="MRP of ₹99.99",
        proves="An unsupported rounding check is withdrawn; a price of 99.99 is not an offence by itself.",
        spec=LabelSpec(mrp=99.99, unit_price=0.50),
        expect={R_ROUNDING: ABSENT},
    ),
    Scenario(
        key="wrong_unit_price",
        title="Unit price that does not follow from the arithmetic",
        proves="The statutory one-gram basis applies to this 200 g single pack. "
               "₹45 / 200 g rounds to ₹0.23 per g; a printed ₹0.20 disagrees.",
        spec=LabelSpec(unit_price=0.20),
        expect={R_UNIT_MATHS: "VIOLATION", R_UNIT_PRICE: "PASS"},
    ),
    Scenario(
        key="english_only",
        title="English-only label",
        proves="Rule 9(4) permits Hindi or English. English alone must pass this script screen.",
        spec=LabelSpec(hindi_net_qty=False),
        expect={R_BILINGUAL: "PASS"},
    ),
    Scenario(
        key="gtin_origin_conflict",
        title="“Made in India” under a GS1 China prefix",
        proves="Barcode forensics as corroboration — flagged for verification, "
               "never asserted as an offence.",
        spec=LabelSpec(gtin=GTIN_CHINA),
        expect={R_GTIN: "INCONCLUSIVE"},
    ),
    Scenario(
        key="exempt_sachet",
        title="6 ml shampoo sachet",
        proves="The exemption pre-filter. A sub-10 g pack must come back exempt, "
               "not with a pile of violations that do not exist in law.",
        spec=LabelSpec(
            brand="Silk Shine", generic="Shampoo", package_category="cosmetic",
            net_qty_value="6", net_qty_unit="ml", net_qty_mm=1.2,
            mrp=3.0, unit_price=None, gtin=None,
            panel_w_mm=60.0, panel_h_mm=90.0,
        ),
        expect={R_NET_QTY: "EXEMPT", R8_HEIGHT: "EXEMPT", R_BILINGUAL: "EXEMPT"},
    ),
    Scenario(
        key="pre_2022_packing",
        title="Pack manufactured in June 2020",
        proves="Temporal rule resolution. The unit-price rules commenced on "
               "1 January 2024; this scenario explicitly assesses the package in June 2020.",
        spec=LabelSpec(packing_date=date(2020, 6, 1), assessment_date=date(2020, 6, 1), unit_price=None),
        expect={R_UNIT_PRICE: ABSENT, R_UNIT_MATHS: ABSENT, R_NET_QTY: "PASS"},
    ),

    # ---- degraded evidence -------------------------------------------------
    # Everything above hands the system a complete, legible label. These hand it
    # what a real inspection produces: one face of a package, or a photograph
    # that failed. The distinction being tested is between a declaration that is
    # missing from the package and one that is missing from the photograph, and
    # getting it wrong means accusing compliant manufacturers.
    Scenario(
        key="missing_mrp_complete",
        title="No MRP anywhere, whole package captured",
        proves="The control for the two scenarios below. With the entire package "
               "in evidence and legible, a declaration that is not found is a "
               "declaration the package does not carry — and that is a violation.",
        spec=LabelSpec(mrp=None),
        expect={R_MRP_PRESENT: "VIOLATION"},
    ),
    Scenario(
        key="partial_capture",
        title="Same missing MRP, but only one face photographed",
        proves="Absence of evidence is not evidence of absence. The MRP really is "
               "missing, yet a single-face capture cannot establish that, so the "
               "system declines to accuse rather than guessing.",
        spec=LabelSpec(mrp=None, covers_all_declarations=False),
        # Food-party declarations need their category-specific legal review;
        # complete manufacturer OCR is not the same as complete applicability.
        expect={R_MRP_PRESENT: "INCONCLUSIVE", R_MANUFACTURER: "INCONCLUSIVE"},
        max_violations=0,
    ),
    Scenario(
        key="unreadable_capture",
        title="A frame with nothing legible on it",
        proves="The failure this was built to stop: a blurred photograph used to "
               "yield six mandatory-declaration violations against a compliant "
               "pack. Nothing readable must produce nothing adverse.",
        spec=LabelSpec(blank_panel=True),
        expect={R_MRP_PRESENT: "INCONCLUSIVE", R_NET_QTY: "INCONCLUSIVE",
                R_MANUFACTURER: "INCONCLUSIVE", R_CONSUMER_CARE: "INCONCLUSIVE"},
        max_violations=0,
    ),
    Scenario(
        key="citizen_advisory",
        title="A real violation, submitted by a citizen",
        proves="The lane gate. The non-conformity is genuine and Tier B evidence "
               "supports it, but a citizen submission is a referral, not evidence "
               "in a proceeding — so it is recorded as an advisory.",
        spec=LabelSpec(net_qty_mm=1.4),
        expect={R8_HEIGHT: "ADVISORY"},
        lane=Lane.CITIZEN,
        max_violations=0,
    ),
    Scenario(
        key="marketing_copy_generic",
        title="“Made with quality spices” as the only commodity word",
        proves="The false pass. A lexicon term buried in marketing copy is not a "
               "declaration of what the commodity is, and must not satisfy Rule "
               "6(1)(b) on a pack that never says what it contains.",
        spec=LabelSpec(generic="Made with quality spices"),
        expect={R_GENERIC: "VIOLATION"},
    ),
    Scenario(
        key="truncated_gtin",
        title="Barcode with its first digit out of frame",
        proves="A GTIN-13 minus its leading digit is a valid GTIN-12 length, so "
               "the check digit fails and the pack looks fraudulent. Restoring "
               "the digit proves it was a framing error, not a fabrication.",
        spec=LabelSpec(gtin="901234567890"),
        expect={R_GTIN_CHECK: "UNVERIFIED"},
        max_violations=0,
    ),
    Scenario(
        key="food_without_a_best_before",
        title="A food pack that never says how long it keeps",
        proves="Whether a best-before duty exists at all is a food-law question, "
               "so this screen stays silent until the commodity category is "
               "confirmed. Once it is food, and the whole pack is in evidence, "
               "the missing declaration is a finding rather than a shrug.",
        spec=LabelSpec(no_best_before=True, package_category="food"),
        expect={R_EXPIRY_FOOD: "VIOLATION"},
    ),
    Scenario(
        key="best_before_on_one_face_only",
        title="The same pack, photographed from one side",
        proves="The companion to the scenario above, and the reason this screen "
               "reads the package rather than a declaration: a date that was not "
               "found may simply be on a face nobody photographed, so a partial "
               "capture cannot convict.",
        spec=LabelSpec(no_best_before=True, package_category="food",
                       covers_all_declarations=False),
        expect={R_EXPIRY_FOOD: "INCONCLUSIVE"},
        max_violations=0,
    ),
]


def scenario(key: str) -> Scenario | None:
    return next((s for s in SCENARIOS if s.key == key), None)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


@dataclass
class Check:
    rule_id: str
    clause: str
    expected: str
    actual: str
    ok: bool


@dataclass
class TruthCheck:
    """A measurement compared against what the renderer actually drew."""

    quantity: str
    drawn: float
    measured: Measured | None
    unit: str = "mm"

    @property
    def error(self) -> float | None:
        return None if self.measured is None else self.measured.value - self.drawn

    @property
    def within_interval(self) -> bool | None:
        if self.measured is None:
            return None
        return self.measured.lower <= self.drawn <= self.measured.upper

    @property
    def error_pct(self) -> float | None:
        if self.measured is None or not self.drawn:
            return None
        return abs(self.measured.value - self.drawn) / self.drawn * 100.0


@dataclass
class BenchResult:
    analysis: Analysis
    truth: GroundTruth
    truth_checks: list[TruthCheck]
    checks: list[Check]
    image: Path
    engine: str
    elapsed_ms: int
    scenario: Scenario | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks) and all(
            t.within_interval is not False for t in self.truth_checks
        )

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


DEFAULT_WORK = Path("data/bench")


def run_spec(
    spec: LabelSpec,
    *,
    engine_name: str = "fixture",
    rules: RulesEngine | None = None,
    work_dir: str | Path = DEFAULT_WORK,
    stem: str = "bench",
    expectations: dict[str, str] | None = None,
    officer: str | None = "BENCH",
    lane: Lane = Lane.FIELD,
) -> BenchResult:
    """Draw a label, run the pipeline over it, compare against ground truth."""
    started = time.perf_counter()
    work = Path(work_dir)
    rendered = render(spec, work, f"{stem}-{uuid.uuid4().hex}")

    # Coverage is *not* asserted here: it travels in the label's own meta.json,
    # written from `spec.covers_all_declarations`. The bench therefore exercises
    # the same attestation path a real capture uses, rather than a back door.
    analysis = analyse(
        [Capture(str(rendered.png), Panel.PDP)],
        AnalyseOptions(lane=lane, operator=officer, engine_name=engine_name,
                       legal_context={"category": spec.package_category, "category_confirmed": True,
                                      "bundle_type": "single", "bundle_confirmed": True,
                                      "shape": "rectangular", "shape_confirmed": True,
                                      "is_imported": spec.is_imported, "imported_confirmed": True,
                                      "assessment_date": spec.assessment_date.isoformat(),
                                      "assessment_date_confirmed": True,
                                      "attested_by": "synthetic_fixture",
                                      "context_basis": "Known generated label specification"}),
        rules=rules,
    )

    by_rule = {f.rule_id: f for f in analysis.findings}

    checks: list[Check] = []
    for rule_id, expected in (expectations or {}).items():
        finding = by_rule.get(rule_id)
        actual = ABSENT if finding is None else finding.verdict.value
        checks.append(
            Check(
                rule_id=rule_id,
                clause=finding.citation.clause if finding else "—",
                expected=expected,
                actual=actual,
                ok=actual == expected,
            )
        )

    truth_checks = [
        TruthCheck(
            "net quantity cap height",
            rendered.truth.net_qty_cap_height_mm,
            analysis.measurements.get("net_quantity_cap_height"),
        ),
        TruthCheck(
            "principal display panel area",
            rendered.truth.pdp_area_cm2,
            analysis.package.pdp_area_cm2,
            unit="cm²",
        ),
    ]

    return BenchResult(
        analysis=analysis,
        truth=rendered.truth,
        truth_checks=truth_checks,
        checks=checks,
        image=rendered.png,
        engine=analysis.engine,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def run_scenario(
    scn: Scenario,
    *,
    engine_name: str = "fixture",
    rules: RulesEngine | None = None,
    work_dir: str | Path = DEFAULT_WORK,
) -> BenchResult:
    result = run_spec(
        scn.spec,
        engine_name=engine_name,
        rules=rules,
        work_dir=work_dir,
        stem=f"scn-{scn.key}",
        expectations=scn.expectations(engine_name),
        lane=scn.lane,
    )
    result.scenario = scn

    if scn.max_violations is not None:
        found = len(result.analysis.violations)
        result.checks.append(
            Check(
                rule_id="(no violation may be recorded)",
                clause="—",
                expected=f"<={scn.max_violations} VIOLATION",
                actual=f"{found} VIOLATION",
                ok=found <= scn.max_violations,
            )
        )
    return result


def run_all(
    *,
    engine_name: str = "fixture",
    rules: RulesEngine | None = None,
    work_dir: str | Path = DEFAULT_WORK,
) -> list[BenchResult]:
    rules = rules or RulesEngine.from_directory()
    return [
        run_scenario(s, engine_name=engine_name, rules=rules, work_dir=work_dir)
        for s in SCENARIOS
    ]


def spec_from_form(form: dict[str, Any]) -> LabelSpec:
    """Build a LabelSpec from the bench form, falling back on the defaults."""

    def num(name: str, default: float | None) -> float | None:
        raw = (form.get(name) or "").strip()
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number")

    def flag(name: str) -> bool:
        return str(form.get(name, "")).lower() in ("1", "true", "on", "yes")

    def num_or_none(name: str) -> float | None:
        """Blank means the declaration is absent from the label, not defaulted."""
        raw = (form.get(name) or "").strip()
        if raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number")

    base = LabelSpec()
    packing = (form.get("packing_date") or "").strip()
    assessment = (form.get("assessment_date") or "").strip()
    try:
        packing_date = date.fromisoformat(packing) if packing else base.packing_date
        assessed_on = date.fromisoformat(assessment) if assessment else base.assessment_date
    except ValueError:
        raise ValueError("Packing and assessment dates must be valid YYYY-MM-DD dates")

    return replace(
        base,
        brand=(form.get("brand") or base.brand).strip(),
        generic=(form.get("generic") or base.generic).strip(),
        package_category=(form.get("package_category") or base.package_category).strip(),
        assessment_date=assessed_on,
        is_imported=flag("is_imported"),
        net_qty_value=(form.get("net_qty_value") or base.net_qty_value).strip(),
        net_qty_unit=(form.get("net_qty_unit") or base.net_qty_unit).strip(),
        net_qty_mm=num("net_qty_mm", base.net_qty_mm),
        hindi_net_qty=flag("hindi_net_qty"),
        mrp=num_or_none("mrp"),
        tax_clause=flag("tax_clause"),
        second_mrp=num("second_mrp", None),
        unit_price=num("unit_price", None),
        unit_price_per=(form.get("unit_price_per") or base.unit_price_per).strip(),
        panel_w_mm=num("panel_w_mm", base.panel_w_mm),
        panel_h_mm=num("panel_h_mm", base.panel_h_mm),
        packing_date=packing_date,
        gtin=(form.get("gtin") or "").strip() or None,
        origin=(form.get("origin") or "").strip() or None,
        scale_source=(form.get("scale_source") or base.scale_source).strip(),
        consumer_care=(form.get("consumer_care") or "").strip() or None,
        covers_all_declarations=flag("covers_all_declarations"),
        blank_panel=flag("blank_panel"),
    )


def spec_to_form(spec: LabelSpec) -> dict[str, Any]:
    """Serialise a spec back into bench-form fields, for the preset buttons."""
    return {
        "brand": spec.brand,
        "generic": spec.generic,
        "package_category": spec.package_category,
        "assessment_date": spec.assessment_date.isoformat(),
        "is_imported": spec.is_imported,
        "net_qty_value": spec.net_qty_value,
        "net_qty_unit": spec.net_qty_unit,
        "net_qty_mm": spec.net_qty_mm,
        "hindi_net_qty": spec.hindi_net_qty,
        "mrp": "" if spec.mrp is None else spec.mrp,
        "tax_clause": spec.tax_clause,
        "second_mrp": "" if spec.second_mrp is None else spec.second_mrp,
        "unit_price": "" if spec.unit_price is None else spec.unit_price,
        "unit_price_per": spec.unit_price_per,
        "panel_w_mm": spec.panel_w_mm,
        "panel_h_mm": spec.panel_h_mm,
        "packing_date": spec.packing_date.isoformat(),
        "gtin": spec.gtin or "",
        "origin": spec.origin or "",
        "consumer_care": spec.consumer_care or "",
        "scale_source": spec.scale_source,
        "covers_all_declarations": spec.covers_all_declarations,
        "blank_panel": spec.blank_panel,
    }
