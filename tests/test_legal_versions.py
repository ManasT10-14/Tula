"""Source-driven regressions for legal applicability, history and narrow screens."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tula.domain.enums import AssuranceTier, Script, Verdict
from tula.domain.enums import DeclarationClass as DC
from tula.domain.models import Declaration, EvidenceCoverage, Measured, PackageFacts, Scan
from tula.extract.normalizers import parse_contact
from tula.rules.engine import RulesEngine
from tula.rules.exemptions import apply, determine
from tula.rules.legal import validate_legal_metadata, validate_legal_screen
from tula.rules.spec import RulePack


@pytest.fixture(scope="module")
def engine():
    return RulesEngine.from_directory()


def package(**overrides):
    context = {"category": "general", "category_confirmed": True,
               "bundle_type": "single", "bundle_confirmed": True,
               "shape": "rectangular", "shape_confirmed": True,
               "is_imported": False, "imported_confirmed": True}
    context.update(overrides)
    return PackageFacts(legal_context=context)


def declarations(quantity=200, unit="g", price=45):
    return {
        DC.NET_QUANTITY: Declaration(klass=DC.NET_QUANTITY, raw=f"Net quantity {quantity} {unit}",
            scripts=[Script.LATIN], norm={"value": quantity, "value_base": quantity,
            "unit": unit, "unit_base": unit, "is_canonical": True}),
        DC.RETAIL_SALE_PRICE: Declaration(klass=DC.RETAIL_SALE_PRICE,
            raw=f"MRP Rs {price} inclusive of all taxes", norm={"value": price, "count": 1}),
    }


def results(engine, decls, *, pkg=None, when=date(2026, 9, 7), measurements=None, packing=None):
    scan = Scan(scan_id="LEGAL", packing_date=packing or when, tier=AssuranceTier.B,
                captured_at=datetime(2026, 9, 7, tzinfo=UTC),
                coverage=EvidenceCoverage.assumed_complete())
    return {f.rule_id: f for f in engine.evaluate_all(
        scan, pkg or package(), decls, measurements or {}, as_of=when)}


CARE = "LMPCR.R6.1.F.CONSUMER_CARE"
UP = "LMPCR.R6.1.F.UNIT_PRICE_PRESENT"
ARITH = "LMPCR.R6.1.F.UNIT_PRICE_ARITHMETIC"
DATE = "LMPCR.R6.1.D.DATE"
HEIGHT = "LMPCR.R8.2.NETQTY_HEIGHT"
ORIGIN = "LMPCR.R6.COUNTRY_OF_ORIGIN"
ROUND = "LMPCR.R11.MRP_ROUNDING"


def test_current_pack_retains_stable_ids_and_primary_source_metadata(engine):
    validate_legal_metadata(engine.pack)
    assert engine.pack.version == "2026.09.07-legal-review-1"
    assert len({r.id for r in engine.pack.rules}) == 18
    assert all(r.sources for r in engine.pack.rules)
    assert engine.pack.by_id(CARE).citation.clause == "Rule 6(2)"
    assert engine.pack.by_id(ORIGIN).effective_from == date(2018, 1, 1)


def test_contact_name_is_not_invented_from_email_or_phone():
    assert parse_contact("1800 123 4567 care@example.com")['has_name'] is False
    assert parse_contact("Customer Services Office\n1800 123 4567")['has_name'] is True


@pytest.mark.parametrize("when,email,expected", [
    (date(2015, 12, 31), False, Verdict.PASS),
    (date(2016, 1, 1), False, Verdict.VIOLATION),
    (date(2026, 9, 7), True, Verdict.PASS),
])
def test_consumer_care_requires_email_from_2016(engine, when, email, expected):
    raw = "Consumer Care Office\n1 Market Road, Delhi 110001\n1800 123 4567"
    if email:
        raw += "\ncare@example.com"
    contact = parse_contact(raw)
    contact["verified_transcription"] = True
    ds = declarations()
    ds[DC.CONSUMER_CARE] = Declaration(klass=DC.CONSUMER_CARE, raw=raw, norm=contact)
    assert results(engine, ds, when=when)[CARE].verdict is expected


def test_unreadable_contact_component_is_not_proved_absence(engine):
    ds = declarations()
    ds[DC.CONSUMER_CARE] = Declaration(klass=DC.CONSUMER_CARE,
        raw="Consumer Care Office 18001234567", norm=parse_contact("Consumer Care Office 18001234567"))
    finding = results(engine, ds)[CARE]
    assert finding.verdict is Verdict.INCONCLUSIVE
    assert "address, email" in finding.detail


@pytest.mark.parametrize("quantity,unit,price,value,basis", [
    (200, "g", 45, 0.23, 1), (1500, "g", 150, 100, 1000),
    (200, "ml", 40, 0.20, 1), (2000, "ml", 100, 50, 1000),
    (50, "cm", 25, 0.50, 1), (150, "cm", 30, 20, 100),
    (5, "N", 50, 10, 1),
])
def test_statutory_unit_price_bases_and_decimal_rounding(engine, quantity, unit, price, value, basis):
    ds = declarations(quantity, unit, price)
    ds[DC.UNIT_SALE_PRICE] = Declaration(klass=DC.UNIT_SALE_PRICE,
        raw=f"Rs {value} per {basis} {unit}", norm={"value": value, "per_base": basis, "unit_base": unit})
    assert results(engine, ds)[ARITH].verdict is Verdict.PASS


@pytest.mark.parametrize("confirmed,expected", [(True, Verdict.VIOLATION), (False, Verdict.INCONCLUSIVE)])
def test_arithmetically_correct_per_100g_is_not_prescribed_per_gram(engine, confirmed, expected):
    ds = declarations()
    ds[DC.UNIT_SALE_PRICE] = Declaration(klass=DC.UNIT_SALE_PRICE, raw="Rs22.50 per100g",
        norm={"value": 22.5, "per_base": 100, "unit_base": "g"})
    finding = results(engine, ds, pkg=package(bundle_confirmed=confirmed))[ARITH]
    assert finding.verdict is expected


def test_missing_unit_price_requires_confirmed_single_package(engine):
    ds = declarations()
    assert results(engine, ds)[UP].verdict is Verdict.VIOLATION
    assert results(engine, ds, pkg=package(bundle_confirmed=False))[UP].verdict is Verdict.INCONCLUSIVE


@pytest.mark.parametrize("bundle", ["combination", "group", "multipiece"])
def test_confirmed_bundle_exception_only_relaxes_unit_price(engine, bundle):
    ds = declarations()
    found = results(engine, ds, pkg=package(bundle_type=bundle))
    assert found[UP].verdict is Verdict.EXEMPT
    assert found["LMPCR.R6.1.E.MRP_PRESENT"].verdict is Verdict.PASS


@pytest.mark.parametrize("quantity,unit", [(1, "g"), (1, "ml"), (1, "cm"), (1, "N")])
def test_equal_mrp_exception(engine, quantity, unit):
    found = results(engine, declarations(quantity, unit))
    assert found[UP].verdict is Verdict.EXEMPT


@pytest.mark.parametrize("quantity,unit", [(1000, "g"), (1000, "ml"), (100, "cm")])
def test_literal_exact_unit_boundary_requires_legal_interpretation(engine, quantity, unit):
    assert results(engine, declarations(quantity, unit))[UP].verdict is Verdict.INCONCLUSIVE


def test_unit_price_different_dimensions_remain_inconclusive(engine):
    ds = declarations()
    ds[DC.UNIT_SALE_PRICE] = Declaration(klass=DC.UNIT_SALE_PRICE, raw="Rs.0.23 per ml",
        norm={"value": 0.23, "per_base": 1, "unit_base": "ml"})
    assert results(engine, ds)[ARITH].verdict is Verdict.INCONCLUSIVE


def test_alcohol_state_excise_context_is_reviewed(engine):
    assert results(engine, declarations(), pkg=package(category="alcohol"))[UP].verdict is Verdict.INCONCLUSIVE


def test_revised_price_does_not_create_an_automatic_offence(engine):
    ds = declarations()
    ds[DC.RETAIL_SALE_PRICE].norm["count"] = 2
    found = results(engine, ds)["LMPCR.R6.1.E.MRP_SINGLE"]
    assert found.verdict is Verdict.INCONCLUSIVE
    assert "revised" in found.detail or "revision" in found.detail


@pytest.mark.parametrize("category,cue,when,expected", [
    ("general", "MFD", date(2026, 9, 7), Verdict.PASS),
    ("general", "PKD", date(2026, 9, 7), Verdict.INCONCLUSIVE),
    ("general", "PKD", date(2020, 3, 1), Verdict.PASS),
    ("food", "MFD", date(2026, 9, 7), Verdict.INCONCLUSIVE),
])
def test_date_roles_and_food_referral(engine, category, cue, when, expected):
    ds = declarations()
    ds[DC.DATE_OF_PACKING] = Declaration(klass=DC.DATE_OF_PACKING, raw=f"{cue} 03/2020",
        norm={"month": 3, "year": 2020})
    assert results(engine, ds, pkg=package(category=category), when=when)[DATE].verdict is expected


def test_missing_generic_date_requires_confirmed_category(engine):
    assert results(engine, declarations())[DATE].verdict is Verdict.VIOLATION
    assert results(engine, declarations(), pkg=package(category_confirmed=False))[DATE].verdict is Verdict.INCONCLUSIVE


def test_confirmed_import_status_controls_origin_without_barcode_inference(engine):
    assert results(engine, declarations())[ORIGIN].verdict is Verdict.NOT_APPLICABLE
    assert results(engine, declarations(), pkg=package(is_imported=True))[ORIGIN].verdict is Verdict.VIOLATION
    assert results(engine, declarations(), pkg=package(imported_confirmed=False))[ORIGIN].verdict is Verdict.INCONCLUSIVE
    assert ORIGIN not in results(engine, declarations(), when=date(2017, 12, 31))


@pytest.mark.parametrize("category,when,exempt", [
    ("tobacco", date(2015, 12, 31), True), ("tobacco", date(2016, 1, 1), False),
    ("pan_masala", date(2026, 1, 31), True), ("pan_masala", date(2026, 2, 1), False),
])
def test_dated_small_package_carveouts(engine, category, when, exempt):
    decision = determine(declarations(8), legal_context=package(category=category).legal_context,
                         as_of=when, policy=engine.pack.legal_policy)
    assert bool(decision.exemptions) is exempt


def test_unknown_small_pack_category_is_reviewed_not_blanket_exempt(engine):
    decision = determine(declarations(8), legal_context={}, policy=engine.pack.legal_policy)
    assert not decision.exemptions
    assert decision.review_reasons
    assert apply(PackageFacts(), decision).is_exempt is False


def test_non_declaration_scope_text_survives_pipeline_engine_recheck(engine):
    decision = determine(declarations(), raw_text="For institutional use only", policy=engine.pack.legal_policy)
    facts = apply(package(), decision)
    finding = results(engine, declarations(), pkg=facts)["LMPCR.R6.1.E.MRP_PRESENT"]
    assert finding.verdict is Verdict.INCONCLUSIVE
    assert "purchaser" in finding.detail


def test_institutional_phrase_requires_actual_transaction_facts(engine):
    suggested = determine(declarations(), raw_text="For institutional use only", policy=engine.pack.legal_policy)
    assert suggested.in_scope and suggested.review_reasons
    confirmed = determine(declarations(), legal_context={"package_class": "institutional", "exemption_confirmed": True}, policy=engine.pack.legal_policy)
    assert confirmed.in_scope is False


def test_explicit_archived_empty_policy_preserves_original_behavior():
    assert determine(declarations(), raw_text="For institutional use only", policy={}).in_scope is False
    assert determine(declarations(8), policy={}).exemptions


def test_archived_pack_content_and_legacy_unit_price_check_remain_available():
    path = Path(__file__).resolve().parents[1] / "rules/lmpcr-2011/archive/2026.09.07-draft.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "legal_policy" not in raw
    assert all("legal_check" not in r for r in raw["rules"])
    old = RulesEngine(RulePack.model_validate(raw))
    ds = declarations()
    ds[DC.UNIT_SALE_PRICE] = Declaration(klass=DC.UNIT_SALE_PRICE, raw="Rs22.50 per100g",
        norm={"value": 22.5, "per_base": 100, "unit_base": "g"})
    assert results(old, ds)[ARITH].verdict is Verdict.PASS


@pytest.mark.parametrize("when,expected", [(date(2019, 1, 1), Verdict.VIOLATION),
    (date(2022, 7, 1), Verdict.INCONCLUSIVE), (date(2026, 9, 7), None)])
def test_historical_rounding_is_dated_and_currently_inactive(engine, when, expected):
    found = results(engine, declarations(price=99.99), when=when)
    assert (found[ROUND].verdict if ROUND in found else None) is expected


def test_old_stock_is_not_automatically_judged_by_later_amendment(engine):
    finding = results(engine, declarations(), packing=date(2020, 1, 1))[UP]
    assert finding.verdict is Verdict.INCONCLUSIVE
    assert "packing date predates" in finding.detail


def test_small_blown_height_uses_corrected_2mm_cell(engine):
    pkg = package()
    pkg.is_blown_formed = True
    pkg.pdp_area_cm2 = Measured(quantity="area", value=40, uncertainty=1, unit="cm2")
    height = Measured(quantity="height", value=1.7, uncertainty=0.1, tier=AssuranceTier.B)
    found = results(engine, declarations(), pkg=pkg, measurements={"net_quantity_cap_height": height})[HEIGHT]
    assert found.verdict is Verdict.VIOLATION
    assert found.threshold == 2.0


def test_height_review_retains_measurement_without_inventing_threshold(engine):
    height = Measured(quantity="height", value=1.7, uncertainty=0.1, tier=AssuranceTier.B)
    found = results(engine, declarations(), pkg=package(shape_confirmed=False),
                    measurements={"net_quantity_cap_height": height})[HEIGHT]
    assert found.verdict is Verdict.INCONCLUSIVE
    assert found.measured == height
    assert found.threshold is None


def test_medical_device_referral_is_dated(engine):
    pkg = package(category="medical_device")
    assert results(engine, declarations(), pkg=pkg)["LMPCR.R6.1.E.MRP_PRESENT"].verdict is Verdict.INCONCLUSIVE
    assert results(engine, declarations(), pkg=pkg, when=date(2025, 10, 23))["LMPCR.R6.1.E.MRP_PRESENT"].verdict is Verdict.PASS


def test_current_enforcement_reference_is_effective_dated(engine):
    key = "LMPCR.R6.1.E.MRP_PRESENT"
    assert "Jan Vishwas" in results(engine, declarations())[key].penalty_ref
    assert "Jan Vishwas" not in results(engine, declarations(), when=date(2026, 4, 30))[key].penalty_ref


@pytest.mark.parametrize("bad", [
    {"kind": "unknown"}, {"kind": "price_uniqueness", "typo": True},
    {"kind": "date", "manufacture_from": "2024-01-01", "manufacture_pattern": "(a+)+$", "review_categories": []},
])
def test_legal_screen_import_rejects_unknown_or_unsafe_metadata(bad):
    with pytest.raises(ValueError):
        validate_legal_screen(bad)


def test_legal_screen_and_context_metadata_must_agree(engine):
    changed = engine.pack.model_copy(deep=True)
    changed.by_id(CARE).assertion['legal_screen']['current_fields'] = ['phone']
    with pytest.raises(ValueError, match="must match"):
        validate_legal_metadata(changed)
