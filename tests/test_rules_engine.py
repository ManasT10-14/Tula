"""Rules engine tests.

The four properties being protected here are the ones that make a report
defensible rather than merely plausible:

  * three-valued logic, so uncertainty propagates instead of defaulting;
  * guard-banded conformity, so nobody is accused on a straddling measurement;
  * temporal resolution, so a 2020 package is judged under 2020's rules;
  * tier gating, so weak evidence cannot sustain a violation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tula.domain.enums import (
    AssuranceTier,
    PackageClass,
    Panel,
    Script,
    Verdict,
)
from tula.domain.enums import (
    DeclarationClass as DC,
)
from tula.domain.models import (
    Declaration,
    EvidenceCoverage,
    Measured,
    PackageFacts,
    Scan,
)
from tula.rules import exemptions
from tula.rules.engine import RulesEngine
from tula.rules.evaluator import EvalContext, evaluate, fuzzy_contains, k_and, k_or

# --------------------------------------------------------------------------
# three-valued logic
# --------------------------------------------------------------------------


def test_kleene_and_prefers_false_over_unknown():
    assert k_and([True, False, None]) is False
    assert k_and([True, None]) is None
    assert k_and([True, True]) is True


def test_kleene_or_prefers_true_over_unknown():
    assert k_or([False, True, None]) is True
    assert k_or([False, None]) is None
    assert k_or([False, False]) is False


def test_missing_facts_yield_unknown_not_false():
    ctx = EvalContext(facts={"decl": {}})
    # absence of a measurement must not silently read as compliance or violation
    assert evaluate(ctx, {"gte_measured": ["$meas.height", 2.0]}) is None


# --------------------------------------------------------------------------
# guard band
# --------------------------------------------------------------------------


def _ctx_with_height(value, uncertainty):
    return EvalContext(
        facts={
            "meas": {
                "h": Measured(quantity="h", value=value, uncertainty=uncertainty,
                              tier=AssuranceTier.B)
            }
        }
    )


def test_guard_band_convicts_only_beyond_the_uncertainty():
    assert evaluate(_ctx_with_height(1.42, 0.18), {"gte_measured": ["$meas.h", 2.0]}) is False


def test_guard_band_passes_only_when_wholly_above():
    assert evaluate(_ctx_with_height(2.60, 0.18), {"gte_measured": ["$meas.h", 2.0]}) is True


def test_guard_band_declines_to_decide_when_straddling():
    # 1.95 +/- 0.20 spans the 2.00 limit -> the evidence does not decide it
    assert evaluate(_ctx_with_height(1.95, 0.20), {"gte_measured": ["$meas.h", 2.0]}) is None


def test_table_lookup_resolves_a_boundary_in_favour_of_the_subject():
    ctx = EvalContext(
        facts={"pkg": PackageFacts(
            pdp_area_cm2=Measured(quantity="a", value=495.0, uncertainty=20.0, unit="cm2"),
            is_blown_formed=False,
        )}
    )
    expr = {
        "gte_measured": [
            "$meas.h",
            {"table_lookup": {
                "key": "$pkg.pdp_area_cm2",
                "rows": [{"max": 100, "normal": 1}, {"max": 500, "normal": 2},
                         {"max": None, "normal": 4}],
                "select": "normal",
                "boundary_policy": "favour_subject",
            }},
        ]
    }
    ctx.facts["meas"] = {"h": Measured(quantity="h", value=3.0, uncertainty=0.1)}
    evaluate(ctx, expr)
    # the interval straddles 500 cm2 where the threshold jumps 2 -> 4 mm;
    # charging the higher band on an uncertain area would manufacture a violation
    assert ctx.trace["threshold"] == 2.0
    assert "favour of the subject" in ctx.trace["threshold_basis"]


def test_fuzzy_phrase_survives_ocr_damage_but_not_absence():
    assert fuzzy_contains("MRP Rs 45 inclusive of all taxes", "inclusive of all taxes")
    assert fuzzy_contains("MRP Rs 45 inclusve of all taxes", "inclusive of all taxes", 0.82)
    assert not fuzzy_contains("MRP Rs. 45.00", "inclusive of all taxes")


# --------------------------------------------------------------------------
# engine-level behaviour
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    return RulesEngine.from_directory()


def _declarations(**overrides):
    base = {
        DC.NET_QUANTITY: Declaration(
            klass=DC.NET_QUANTITY, raw="Net Wt. 200 g", panel=Panel.PDP,
            scripts=[Script.LATIN, Script.DEVANAGARI],
            norm={"value": 200.0, "unit": "g", "is_canonical": True,
                  "value_base": 200.0, "unit_base": "g"},
        ),
        DC.RETAIL_SALE_PRICE: Declaration(
            klass=DC.RETAIL_SALE_PRICE,
            raw="MRP Rs. 45.00 inclusive of all taxes", panel=Panel.PDP,
            norm={"value": 45.0, "count": 1, "has_tax_clause": True},
        ),
    }
    base.update(overrides)
    return base


def _scan(tier=AssuranceTier.B, packing=date(2026, 3, 1), coverage=None):
    """A scan whose declarations are supplied directly, so coverage is total.

    Stated explicitly rather than defaulted: `Scan` deliberately starts with
    *no* coverage, so any caller that forgets to establish what it actually
    photographed gets inconclusive verdicts rather than accusations. These tests
    hand the engine its declarations, so the whole package is by construction in
    evidence and the presence checks are entitled to decide.
    """
    return Scan(scan_id="TEST", tier=tier, packing_date=packing,
                captured_at=datetime(2026, 4, 1, tzinfo=UTC),
                coverage=coverage or EvidenceCoverage.assumed_complete())


def _verdict(findings, rule_id):
    return next((f.verdict for f in findings if f.rule_id == rule_id), None)


def _confirmed_package(**kwargs):
    """Fixture truth, not inferred confirmation for real captures."""
    return PackageFacts(legal_context={"category": "general", "category_confirmed": True,
        "bundle_type": "single", "bundle_confirmed": True,
        "shape": "rectangular", "shape_confirmed": True}, **kwargs)


def test_pack_loads_and_every_rule_is_unique(engine):
    assert len(engine.pack.rules) >= 18
    assert len({r.id for r in engine.pack.rules}) == len(engine.pack.rules)


def test_temporal_resolution_excludes_a_rule_not_yet_in_force(engine):
    # Final commencement of this current unit-price screen is 1 January 2024.
    old = engine.evaluate_all(
        _scan(packing=date(2020, 6, 1)), PackageFacts(), _declarations(), {}
    )
    assert _verdict(old, "LMPCR.R6.1.F.UNIT_PRICE_PRESENT") is None

    new = engine.evaluate_all(_scan(), _confirmed_package(), _declarations(), {})
    assert _verdict(new, "LMPCR.R6.1.F.UNIT_PRICE_PRESENT") is Verdict.VIOLATION


def test_tier_gate_downgrades_a_violation_on_weak_evidence(engine):
    package = _confirmed_package(
        pdp_area_cm2=Measured(quantity="a", value=216.0, uncertainty=8.6, unit="cm2")
    )
    measurements = {
        "net_quantity_cap_height": Measured(
            quantity="h", value=1.42, uncertainty=0.18, tier=AssuranceTier.B
        )
    }

    strong = engine.evaluate_all(_scan(AssuranceTier.B), package, _declarations(), measurements)
    assert _verdict(strong, "LMPCR.R8.2.NETQTY_HEIGHT") is Verdict.VIOLATION

    # same measurement, weaker evidence: a citizen photo may not convict
    weak = engine.evaluate_all(_scan(AssuranceTier.C), package, _declarations(), measurements)
    assert _verdict(weak, "LMPCR.R8.2.NETQTY_HEIGHT") is Verdict.INCONCLUSIVE


def test_unit_price_arithmetic_audit(engine):
    correct = _declarations(
        **{DC.UNIT_SALE_PRICE: Declaration(
            klass=DC.UNIT_SALE_PRICE, raw="Rs 0.23 per 1 g",
            norm={"value": 0.23, "per_base": 1.0, "unit_base": "g"})}
    )
    wrong = _declarations(
        **{DC.UNIT_SALE_PRICE: Declaration(
            klass=DC.UNIT_SALE_PRICE, raw="Rs 0.20 per 1 g",
            norm={"value": 0.20, "per_base": 1.0, "unit_base": "g"})}
    )
    rule = "LMPCR.R6.1.F.UNIT_PRICE_ARITHMETIC"
    assert _verdict(engine.evaluate_all(_scan(), _confirmed_package(), correct, {}), rule) is Verdict.PASS
    assert _verdict(engine.evaluate_all(_scan(), _confirmed_package(), wrong, {}), rule) is Verdict.VIOLATION


def test_rule_9_4_accepts_an_english_only_label(engine):
    english_only = _declarations()
    english_only[DC.NET_QUANTITY] = english_only[DC.NET_QUANTITY].model_copy(
        update={"scripts": [Script.LATIN]}
    )
    findings = engine.evaluate_all(_scan(), PackageFacts(), english_only, {})
    assert _verdict(findings, "LMPCR.R9.3.BILINGUAL") is Verdict.PASS

    bilingual = engine.evaluate_all(_scan(), PackageFacts(), _declarations(), {})
    assert _verdict(bilingual, "LMPCR.R9.3.BILINGUAL") is Verdict.PASS


def test_declaration_on_the_bottom_panel_is_a_violation(engine):
    decls = _declarations()
    decls[DC.RETAIL_SALE_PRICE] = decls[DC.RETAIL_SALE_PRICE].model_copy(
        update={"panel": Panel.BOTTOM}
    )
    findings = engine.evaluate_all(_scan(), PackageFacts(), decls, {})
    assert _verdict(findings, "LMPCR.R9.2.NOT_ON_BOTTOM") is None


def test_uncaptured_panel_is_inconclusive_not_compliant(engine):
    decls = _declarations()
    decls[DC.RETAIL_SALE_PRICE] = decls[DC.RETAIL_SALE_PRICE].model_copy(
        update={"panel": Panel.UNKNOWN}
    )
    findings = engine.evaluate_all(_scan(), PackageFacts(), decls, {})
    assert _verdict(findings, "LMPCR.R9.2.NOT_ON_BOTTOM") is None


def test_mrp_rounding(engine):
    decls = _declarations()
    decls[DC.RETAIL_SALE_PRICE] = decls[DC.RETAIL_SALE_PRICE].model_copy(
        update={"norm": {"value": 99.99, "count": 1}}
    )
    findings = engine.evaluate_all(_scan(), PackageFacts(), decls, {})
    assert _verdict(findings, "LMPCR.R11.MRP_ROUNDING") is None


# --------------------------------------------------------------------------
# exemptions -- the largest false-positive source in a naive build
# --------------------------------------------------------------------------


def test_small_sachet_is_exempt_not_a_pile_of_violations(engine):
    decls = {
        DC.NET_QUANTITY: Declaration(
            klass=DC.NET_QUANTITY, raw="Net Wt. 8 g",
            norm={"value": 8.0, "unit": "g", "is_canonical": True,
                  "value_base": 8.0, "unit_base": "g"},
        )
    }
    facts = _confirmed_package()
    determination = exemptions.determine(decls, legal_context=facts.legal_context)
    assert determination.exemptions, "confirmed ordinary sub-10 g category receives Rule 26(a) relief"

    package = exemptions.apply(facts, determination)
    findings = engine.evaluate_all(_scan(), package, decls, {})
    assert not [f for f in findings if f.verdict is Verdict.VIOLATION]
    assert all(f.verdict is Verdict.EXEMPT for f in findings)


def test_tobacco_is_carved_out_of_the_small_package_exemption():
    decls = {
        DC.NET_QUANTITY: Declaration(
            klass=DC.NET_QUANTITY, raw="Net Wt. 8 g",
            norm={"value": 8.0, "value_base": 8.0, "unit_base": "g"},
        ),
        DC.GENERIC_NAME: Declaration(
            klass=DC.GENERIC_NAME, raw="Chewing tobacco", norm={"name": "tobacco"}
        ),
    }
    assert not exemptions.determine(decls).exemptions


def test_institutional_pack_is_out_of_scope_entirely():
    decls = {
        DC.NET_QUANTITY: Declaration(
            klass=DC.NET_QUANTITY, raw="Net Wt. 25 kg",
            norm={"value": 25.0, "value_base": 25000.0, "unit_base": "g"},
        )
    }
    determination = exemptions.determine(decls, raw_text="For institutional use only",
        legal_context={"package_class": "institutional", "exemption_confirmed": True})
    assert determination.in_scope is False
    assert determination.package_class is PackageClass.INSTITUTIONAL


def test_retail_pack_carries_the_full_duty():
    determination = exemptions.determine(_declarations())
    assert determination.in_scope
    assert determination.exemptions == []
    assert "no exemption established" in determination.summary


def test_unreadable_script_yields_inconclusive_not_violation(engine):
    """Rule 9(3) must not convict on our own inability to read the label."""
    decls = _declarations()
    decls[DC.NET_QUANTITY] = decls[DC.NET_QUANTITY].model_copy(
        update={"scripts": [Script.UNREADABLE]}
    )
    findings = engine.evaluate_all(_scan(), PackageFacts(), decls, {})
    finding = next(f for f in findings if f.rule_id == "LMPCR.R9.3.BILINGUAL")
    assert finding.verdict is Verdict.INCONCLUSIVE
    assert "could not transcribe" in finding.detail
