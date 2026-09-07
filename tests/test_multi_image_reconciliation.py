"""Conflicting injected OCR fixtures must not become chosen legal facts.

These are aggregation/integration regressions, not real-photo OCR accuracy tests.
"""
from datetime import date

import pytest
from PIL import Image

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Panel, Verdict
from tula.domain.models import Analysis, PackageFacts, Scan, sha256_file
from tula.extract.pipeline import extract
from tula.ocr.base import OcrLine, OcrResult
from tula.rules import exemptions
from tula.rules.engine import RulesEngine
from tula.security import User
from tula.services import review
from tula.storage.db import Repository

CONTEXT = {"category": "general", "category_confirmed": True, "bundle_type": "single",
           "bundle_confirmed": True, "shape": "rectangular", "shape_confirmed": True,
           "imported_confirmed": True, "is_imported": False,
           "assessment_date": "2026-09-07", "assessment_date_confirmed": True}


def image(name, *texts, confidence=.99, review_required=False, alternatives=()):
    return Panel.BACK, OcrResult(lines=[
        OcrLine(text, (10, 10 + i * 60, 520, 40 + i * 60), height_px=20,
                confidence=confidence, frame=name, review_required=review_required,
                alternatives=list(alternatives)) for i, text in enumerate(texts)],
        width=640, height=480)


@pytest.fixture(scope="module")
def rules():
    return RulesEngine.from_directory()


def findings(result, rules, context=None):
    return {item.rule_id: item.verdict for item in rules.evaluate_all(
        Scan(scan_id="AGGREGATION-QA", packing_date=date(2026, 3, 1)),
        PackageFacts(legal_context=context or CONTEXT), result.declarations, {})}


@pytest.mark.parametrize("reverse", [False, True])
def test_quantity_conflict_preserves_all_sources_and_never_exempts_small_pack(reverse, rules):
    images = [image("front.png", "Net wt 5 g"), image("back.png", "Net wt 500 g")]
    result = extract(images[::-1] if reverse else images)
    value = result.declarations[DC.NET_QUANTITY]
    assert value.norm["requires_review"] and value.norm["value_base"] is None
    assert value.norm["value"] is None
    assert value.provenance.status == "needs_review"
    assert {s.frame for s in value.provenance.sources} == {"front.png", "back.png"}
    assert {item["value_base"] for item in value.norm["candidates"]} == {5, 500}
    determination = exemptions.determine(result.declarations, legal_context=CONTEXT,
        as_of=date(2026, 9, 7), policy=rules.pack.legal_policy)
    assert not determination.exemptions
    verdicts = findings(result, rules)
    assert verdicts["LMPCR.R6.1.C.NET_QUANTITY"] is Verdict.INCONCLUSIVE
    assert Verdict.EXEMPT not in verdicts.values()


@pytest.mark.parametrize("reverse", [False, True])
def test_equivalent_units_and_non_net_roles_do_not_create_conflicts(reverse):
    images = [image("front.png", "Net wt 500 g", "Gross weight 550 g"),
              image("back.png", "Net quantity 0.5 kg", "Unit price Rs 0.20 per g")]
    value = extract(images[::-1] if reverse else images).declarations[DC.NET_QUANTITY]
    assert value.norm["value_base"] == 500
    assert not value.norm.get("requires_review")
    assert {s.frame for s in value.provenance.sources} == {"front.png", "back.png"}
    assert len(value.norm["observations"]) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_low_confidence_duplicate_quantity_keeps_conflict_alternatives(reverse):
    images = [image("clear.png", "Net wt 500 g"), image("uncertain.png", "Net wt 500 g",
        confidence=.54, review_required=True,
        alternatives=[{"text": "Net wt 800 g", "confidence": .91, "variant": "other"}])]
    value = extract(images[::-1] if reverse else images).declarations[DC.NET_QUANTITY]
    assert value.norm["requires_review"] and value.norm["value_base"] is None
    assert value.confidence == .54
    assert value.norm["candidates"][0]["ocr_alternatives"][0]["text"] == "Net wt 800 g"


@pytest.mark.parametrize("texts", [
    ("Unit price Rs 0.20 per g", "Unit price Rs 0.25 per g"),
    ("MRP Rs 40", "Revised MRP Rs 50"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_price_conflicts_never_supply_arithmetic(texts, reverse, rules):
    images = [image("a.png", texts[0]), image("b.png", texts[1]),
              image("context.png", "Net wt 200 g",
                    "MRP Rs 40" if texts[0].startswith("Unit") else "Unit price Rs 0.20 per g")]
    result = extract(images[::-1] if reverse else images)
    kind = DC.UNIT_SALE_PRICE if texts[0].startswith("Unit") else DC.RETAIL_SALE_PRICE
    value = result.declarations[kind]
    assert value.norm["requires_review"] and value.norm["value"] is None
    assert len(value.norm["candidates"]) >= 2
    assert findings(result, rules)["LMPCR.R6.1.F.UNIT_PRICE_ARITHMETIC"] in {
        Verdict.INCONCLUSIVE, Verdict.UNVERIFIED}


def test_two_prices_in_one_region_remain_reviewable_without_revised_price_accusation():
    price = extract([image("price.png", "Original MRP Rs 100 Revised MRP Rs 90")]).declarations[DC.RETAIL_SALE_PRICE]
    assert price.norm["requires_review"] and price.norm["value"] is None
    assert price.norm["distinct_values"] == [90, 100] and price.norm["count"] == 2
    assert "Original MRP" in price.raw


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_origins_are_candidates_not_a_selected_country(reverse):
    images = [image("front.png", "Made in India"), image("back.png", "Country of origin: Nepal")]
    value = extract(images[::-1] if reverse else images).declarations[DC.COUNTRY_OF_ORIGIN]
    assert value.norm["requires_review"] and value.norm["country"] is None
    assert {item["country"] for item in value.norm["candidates"]} == {"India", "Nepal"}


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_same_date_role_withholds_chronology(reverse, rules):
    images = [image("front.png", "MFG 03/2023", "Net wt 500 g"),
              image("back.png", "MFG 09/2026", "MRP Rs 100")]
    result = extract(images[::-1] if reverse else images)
    value = result.declarations[DC.DATE_OF_PACKING]
    assert value.norm["requires_review"] and value.norm["date_evidence_uncertain"]
    assert value.norm["year"] is None and value.norm["month"] is None
    assert {s.frame for s in value.provenance.sources} == {"front.png", "back.png"}
    assert {item["iso"] for item in value.norm["candidates"]} == {"2023-03", "2026-09"}
    assert findings(result, rules)["LMPCR.R6.1.D.DATE"] is Verdict.INCONCLUSIVE
    assert findings(result, rules)["LMPCR.R6.1.F.UNIT_PRICE_PRESENT"] is Verdict.INCONCLUSIVE


@pytest.mark.parametrize("reverse", [False, True])
def test_manufacture_and_packing_are_distinct_events(reverse):
    images = [image("front.png", "MFG 03/2026"), image("back.png", "PKD 04/2026")]
    value = extract(images[::-1] if reverse else images).declarations[DC.DATE_OF_PACKING]
    assert not value.norm.get("requires_review") and not value.norm["date_evidence_uncertain"]
    assert value.norm["date_role"] == "manufacturing_date" and value.norm["month"] == 3
    assert value.norm["legal_date_role"] == "packing_date"
    assert value.norm["date_events"]["packing_date"]["value"]["month"] == 4
    assert value.frame == "front.png"


@pytest.mark.parametrize("role", ["MFG", "PKD"])
def test_two_event_roles_in_one_ocr_line_are_not_overwritten(role):
    text = "MFG 03/2026 PKD 04/2026" if role == "MFG" else "PKD 04/2026 MFG 03/2026"
    value = extract([image("label.png", text)]).declarations[DC.DATE_OF_PACKING]
    assert value.norm["month"] == 3 and not value.norm.get("requires_review")
    assert value.norm["date_events"]["packing_date"]["value"]["month"] == 4


@pytest.mark.parametrize("field,text", [("ingredients", "Ingredients: milk"),
                                         ("expiry_date", "EXP 09/2026")])
@pytest.mark.parametrize("reverse", [False, True])
def test_supplementary_duplicates_merge_uncertainty_without_losing_alternatives(field, text, reverse):
    alternative = text.replace("milk", "soy").replace("09/2026", "08/2026")
    images = [image("clear.png", text), image("uncertain.png", text, confidence=.54,
        review_required=True, alternatives=[{"text": alternative, "confidence": .91, "variant": "other"}])]
    result = extract(images[::-1] if reverse else images, allergen_concerns=["milk"])
    values = result.intelligence["fields"][field]
    assert len(values) == 1
    value = values[0]
    assert value["status"] == "needs_review" and value["ocr_confidence"] == .54
    assert len(value["sources"]) == 2
    assert value["ocr_alternatives"][0]["text"] == alternative
    assert "temporal_status" not in value
    if field == "ingredients":
        match = result.intelligence["allergens"]["matches"][0]
        assert match["status"] == "needs_review" and match["ocr_confidence"] == .54


def test_cue_on_another_image_is_preserved_but_does_not_invent_a_conflicting_value():
    result = extract([image("unresolved.png", "MRP", "EXP"),
                      image("readable.png", "MRP Rs 140", "EXP 09/2026")])
    for key in ("retail_sale_price", "expiry_date"):
        values = result.intelligence["fields"][key]
        assert any(item["value"] is None and item["sources"][0]["frame"] == "unresolved.png" for item in values)
        assert any(item["value"] is not None for item in values)
    assert result.declarations[DC.RETAIL_SALE_PRICE].norm["value"] == 140


def test_analyse_uses_valid_packing_event_and_fences_all_conflicting_frames(tmp_path):
    paths = []
    for name in ("a", "b", "c"):
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (640, 480), "white").save(path)
        paths.append(str(path))
    class InjectedOCR:
        name = "aggregation-injected-ocr-fixture"

        def read(self, path):
            date_text = {paths[0]: "MFG 03/2026", paths[1]: "MFG 02/2026", paths[2]: "PKD 04/2026"}[path]
            return image(path, date_text, "Net wt 500 g", "MRP Rs 100")[1]

    result = analyse([Capture(path, Panel.BACK) for path in paths],
                     AnalyseOptions(legal_context=CONTEXT), engine=InjectedOCR())
    assert result.scan.packing_date == date(2026, 4, 1)
    assert set(paths[:2]) <= set(result.scan.coverage.unreadable_frames)
    assert result.declarations[DC.DATE_OF_PACKING].norm["requires_review"]
    assert not result.declarations[DC.DATE_OF_PACKING].norm["date_evidence_uncertain"]


def test_audited_date_correction_clears_derived_uncertainty_and_preserves_originals(tmp_path, rules):
    paths = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (640, 480), "white").save(path)
        paths.append(str(path))
    result = extract([image(paths[0], "MFG 03/2023"), image(paths[1], "MFG 03/2026")])
    owner = User("aggregation-officer", "aggregation", "Aggregation QA officer", "inspector", True)
    analysis = Analysis(scan=Scan(scan_id="AGGREGATION-CORRECTION-QA", frames=paths,
        frame_hashes={path: sha256_file(path) for path in paths}, inspector_id=owner.id, source="bench"),
        package=PackageFacts(legal_context=CONTEXT), declarations=result.declarations,
        intelligence=result.intelligence, rules_version=rules.pack.version)
    repo = Repository(tmp_path / "qa.db")
    repo.archive_rules(rules.pack)
    repo.save(analysis)
    changed = review.correct(repo, rules, analysis.scan.scan_id, 0, owner, DC.DATE_OF_PACKING,
        "MFG 03/2026", 1, [10, 10, 520, 40], "Verified the complete printed event against the retained original.")
    value = changed.declarations[DC.DATE_OF_PACKING]
    assert not value.norm.get("date_evidence_uncertain") and not value.norm.get("requires_review")
    assert changed.scan.packing_date == date(2026, 3, 1)
    assert len(value.provenance.original_sources) == 2
    assert repo.revision(analysis.scan.scan_id, 0).declarations[DC.DATE_OF_PACKING].norm["date_evidence_uncertain"]
    assert not changed.package.legal_context.get("legal_review_reasons")
    assert next(f.verdict for f in changed.findings if f.rule_id == "LMPCR.R6.1.D.DATE") is Verdict.PASS


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind,texts,key", [
    (DC.BRAND, ("Brand: Meridian", "Brand: Juniper"), "name"),
    (DC.GENERIC_NAME, ("Shampoo", "Soap"), "name"),
    (DC.MANUFACTURER, ("Manufactured by Meridian Ltd", "Manufactured by Juniper Ltd"), "name"),
    (DC.CONSUMER_CARE, ("Consumer care +91 9876543210", "Consumer care +91 9876543211"), "phone"),
])
def test_other_primary_field_conflicts_preserve_candidates_and_withhold_a_winner(reverse, kind, texts, key):
    images = [image("a.png", texts[0]), image("b.png", texts[1])]
    value = extract(images[::-1] if reverse else images).declarations[kind]
    assert value.norm["requires_review"] and value.norm[key] is None
    assert len(value.norm["candidates"]) == 2
    assert {s.frame for s in value.provenance.sources} == {"a.png", "b.png"}


@pytest.mark.parametrize("reverse", [False, True])
def test_manufacturer_packer_and_importer_are_not_conflicting_party_names(reverse):
    images = [image("a.png", "Manufactured by Meridian Ltd"),
              image("b.png", "Packed by Juniper Ltd"),
              image("c.png", "Imported by Aspen Ltd")]
    value = extract(images[::-1] if reverse else images).declarations[DC.MANUFACTURER]
    assert not value.norm.get("requires_review")
    assert value.norm["name"] == "Manufactured by Meridian Ltd"
    assert {item["role"] for item in value.norm["observations"]} == {"manufacturer", "packer", "importer"}
    assert {s.frame for s in value.provenance.sources} == {"a.png", "b.png", "c.png"}


@pytest.mark.parametrize("reverse", [False, True])
def test_contact_components_are_not_combined_across_images(reverse):
    images = [image("a.png", "Consumer care", "Plot 22, Pune 411018"),
              image("b.png", "Consumer care", "info@example.test +91 9876543210")]
    value = extract(images[::-1] if reverse else images).declarations[DC.CONSUMER_CARE]
    assert not (value.norm.get("has_address") and value.norm.get("has_email") and value.norm.get("has_phone"))
    assert len(value.norm["observations"]) == 2
    assert {s.frame for s in value.provenance.sources} == {"a.png", "b.png"}


@pytest.mark.parametrize("reverse", [False, True])
def test_repeated_supported_names_keep_all_sources_without_false_conflict(reverse):
    images = [image("a.png", "Brand: Meridian", "Tea"), image("b.png", "Brand: Meridian", "Green tea")]
    result = extract(images[::-1] if reverse else images)
    for kind in (DC.GENERIC_NAME, DC.BRAND):
        value = result.declarations[kind]
        assert not value.norm.get("requires_review")
        assert {s.frame for s in value.provenance.sources} == {"a.png", "b.png"}


@pytest.mark.parametrize("reverse", [False, True])
def test_different_days_of_same_date_event_are_still_conflicting(reverse):
    images = [image("a.png", "MFG 15/03/2026"), image("b.png", "MFG 16/03/2026")]
    value = extract(images[::-1] if reverse else images).declarations[DC.DATE_OF_PACKING]
    assert value.norm["requires_review"] and value.norm["year"] is None
    assert {item["day"] for item in value.norm["candidates"]} == {15, 16}


def test_month_only_closeup_can_agree_with_complete_date_without_inventing_day():
    value = extract([image("a.png", "MFG 03/2026"), image("b.png", "MFG 15/03/2026")]).declarations[DC.DATE_OF_PACKING]
    assert not value.norm.get("requires_review") and value.norm["month"] == 3
    assert len(value.norm["observations"]) == 2
    assert {item["value"].get("day") for item in value.norm["observations"]} == {None, 15}


def test_unresolved_packing_event_cannot_borrow_manufacturing_chronology():
    value = extract([image("a.png", "MFG 03/2026", "PKD 04/2026"),
                     image("b.png", "PKD 05/2026")]).declarations[DC.DATE_OF_PACKING]
    assert value.norm["month"] == 3 and not value.norm.get("requires_review")
    assert value.norm["date_evidence_uncertain"]
    assert value.norm["date_events"]["packing_date"]["value"] is None
    assert len(value.norm["date_events"]["packing_date"]["observations"]) == 2


def test_caller_context_cannot_set_or_clear_derived_date_uncertainty(rules):
    clear = extract([image("a.png", "MFG 03/2026", "Net wt 500 g", "MRP Rs 100")])
    assert not exemptions.determine(clear.declarations,
        legal_context={**CONTEXT, "date_evidence_uncertain": True}).review_reasons
    uncertain = extract([image("a.png", "MFG 03/2023", "Net wt 5 g"),
                         image("b.png", "MFG 03/2026")])
    verdicts = findings(uncertain, rules, {**CONTEXT, "date_evidence_uncertain": False})
    assert Verdict.EXEMPT not in verdicts.values()
    assert Verdict.PASS not in verdicts.values()


def test_legacy_exemption_cannot_consume_an_explicitly_uncertain_quantity():
    result = extract([image("a.png", "Net wt 5 g")])
    # A historical/manual API object may still retain a value alongside the flag.
    result.declarations[DC.NET_QUANTITY].norm["requires_review"] = True
    assert not exemptions.determine(result.declarations, policy={}).exemptions


@pytest.mark.parametrize("reverse", [False, True])
def test_equivalent_decimal_units_are_not_conflicts_from_binary_rounding(reverse):
    images = [image("a.png", "Net wt 1.001 kg", "Unit price Rs 12 per 1.001 kg"),
              image("b.png", "Net wt 1001 g", "Unit price Rs 12 per 1001 g")]
    result = extract(images[::-1] if reverse else images)
    for kind in (DC.NET_QUANTITY, DC.UNIT_SALE_PRICE):
        assert not result.declarations[kind].norm.get("requires_review")
        assert all(item["status"] == "detected" for item in result.intelligence["fields"][kind.value])


def _fused_run(text):
    return extract([(Panel.BACK, OcrResult(lines=[OcrLine(text, (10, 10, 610, 40),
        confidence=.99, height_px=20, frame="fused-fixture.png")], width=640, height=480))])


def _assert_fused_evidence(field, text):
    observations = field.norm["observations"]
    for item in observations:
        begin, end = item["text_range"]
        assert item["raw"] == text[begin:end]
        assert item["region_text"] == text
        assert item["sources"][0]["text"] == text
        assert item["sources"][0]["bbox"] == [10, 10, 610, 40]


@pytest.mark.parametrize("text", ["Net wt 5 g Net wt 500 g", "Net wt 500 g Net wt 5 g"])
def test_repeated_quantity_cues_cannot_grant_small_pack_relief(text):
    result = _fused_run(text)
    field = result.declarations[DC.NET_QUANTITY]
    assert field.norm["requires_review"] and field.norm["value_base"] is None
    assert {item["value_base"] for item in field.norm["candidates"]} == {5, 500}
    assert not exemptions.determine(result.declarations, legal_context={"category": "general", "category_confirmed": True},
                         as_of=date(2026, 9, 7)).exemptions
    _assert_fused_evidence(field, text)


@pytest.mark.parametrize("text", ["MFG 03/2023 MFG 09/2026", "MFG 09/2026 MFG 03/2023"])
def test_repeated_same_date_cues_withhold_legal_date(text):
    field = _fused_run(text).declarations[DC.DATE_OF_PACKING]
    assert field.norm["requires_review"] and field.norm["date_evidence_uncertain"]
    assert field.norm["year"] is None and field.norm["month"] is None
    assert {item["iso"] for item in field.norm["candidates"]} == {"2023-03", "2026-09"}
    _assert_fused_evidence(field, text)


@pytest.mark.parametrize("text", ["Net wt 500 g Net quantity 0.5 kg", "Net quantity 0.5 kg Net wt 500 g"])
def test_agreeing_repeated_quantities_keep_both_windows(text):
    field = _fused_run(text).declarations[DC.NET_QUANTITY]
    assert not field.norm.get("requires_review") and field.norm["value_base"] == 500
    assert len(field.norm["observations"]) == 2
    _assert_fused_evidence(field, text)


@pytest.mark.parametrize("text", ["MFG 03/2026 PKD 04/2026 MFG 03/2026",
                                  "PKD 04/2026 MFG 03/2026 MFG 03/2026"])
def test_agreeing_manufacturing_windows_do_not_overwrite_packing_role(text):
    field = _fused_run(text).declarations[DC.DATE_OF_PACKING]
    assert not field.norm.get("requires_review") and field.norm["month"] == 3
    assert field.norm["date_events"]["packing_date"]["value"]["month"] == 4
    assert len(field.norm["observations"]) == 2
    _assert_fused_evidence(field, text)


@pytest.mark.parametrize("text", ["MFG 03/2026 EXP 09/2026 MFG 04/2026 Best before 10/2026",
                                  "MFG 04/2026 Best before 10/2026 MFG 03/2026 EXP 09/2026"])
def test_expiry_and_best_before_do_not_become_manufacturing_candidates(text):
    field = _fused_run(text).declarations[DC.DATE_OF_PACKING]
    assert {item["month"] for item in field.norm["candidates"]} == {3, 4}
    _assert_fused_evidence(field, text)


def test_unresolved_repeated_cue_does_not_borrow_neighboring_expiry():
    text = "MFG EXP 09/2026 MFG 03/2026"
    field = _fused_run(text).declarations[DC.DATE_OF_PACKING]
    assert field.norm["month"] == 3
    assert {item["value"]["month"] for item in field.norm["observations"] if item["value"]} == {3}
    assert any(item["value"] is None for item in field.norm["observations"])
    _assert_fused_evidence(field, text)


def _assert_supplementary_windows(values, text):
    assert len(values) == 2
    assert len({tuple(item["text_range"]) for item in values}) == 2
    for item in values:
        begin, end = item["text_range"]
        assert item["raw"] == text[begin:end]
        assert item["region_text"] == text
        assert item["sources"][0]["text"] == text
        assert item["sources"][0]["bbox"] == [10, 10, 610, 40]
        assert item["sources"][0]["frame"] == "fused-fixture.png"
        assert item["ocr_confidence"] == .99
        assert item["method"] == "repeated_cue_text_window"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("second,conflicting", [("Net wt 5 g", True), ("Net quantity 0.5 kg", False)])
def test_supplementary_fused_quantity_matches_primary_uncertainty(reverse, second, conflicting):
    parts = ["Net wt 500 g", second]
    text = " ".join(parts[::-1] if reverse else parts)
    result = _fused_run(text)
    values = result.intelligence["fields"]["net_quantity"]
    _assert_supplementary_windows(values, text)
    assert {item["status"] for item in values} == {"needs_review" if conflicting else "detected"}
    assert {item["value"]["value_base"] for item in values} == ({5, 500} if conflicting else {500})
    assert bool(result.declarations[DC.NET_QUANTITY].norm.get("requires_review")) is conflicting


@pytest.mark.parametrize("field,cue", [("manufacturing_date", "MFG"), ("packing_date", "PKD"),
    ("expiry_date", "EXP"), ("best_before", "Best before"), ("use_by", "Use by")])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("second,conflicting", [("03/2023", True), ("09/2026", False)])
def test_supplementary_fused_date_windows_reconcile_without_temporal_overclaim(field, cue, reverse, second, conflicting):
    parts = [f"{cue} 09/2026", f"{cue} {second}"]
    text = " ".join(parts[::-1] if reverse else parts)
    result = _fused_run(text)
    values = result.intelligence["fields"][field]
    _assert_supplementary_windows(values, text)
    assert {item["status"] for item in values} == {"needs_review" if conflicting else "detected"}
    assert {item["value"] for item in values} == ({"2023-03", "2026-09"} if conflicting else {"2026-09"})
    for item in values:
        assert [candidate["iso"] for candidate in item["candidates"]] == [item["value"]]
        if conflicting:
            assert "temporal_status" not in item and "as_of" not in item
        elif field in {"expiry_date", "best_before", "use_by"}:
            assert item.get("temporal_status") in {"printed_date_passed", "printed_date_not_passed"}
            assert item.get("as_of")
    if field in {"manufacturing_date", "packing_date"}:
        event = result.declarations[DC.DATE_OF_PACKING].norm["date_events"][field]
        assert (event["status"] == "needs_review") is conflicting


def test_supplementary_repeated_date_keeps_unresolved_window_and_separate_event_roles():
    text = "MFG EXP 09/2026 MFG 03/2026 PKD 04/2026"
    values = _fused_run(text).intelligence["fields"]
    _assert_supplementary_windows(values["manufacturing_date"], text)
    assert {(item["value"], item["status"]) for item in values["manufacturing_date"]} == {
        (None, "needs_review"), ("2026-03", "detected")}
    assert [item["value"] for item in values["expiry_date"]] == ["2026-09"]
    assert [item["value"] for item in values["packing_date"]] == ["2026-04"]


def test_supplementary_fused_windows_preserve_disputed_original_and_alternatives():
    text = "MFG 03/2026 MFG 03/2026"
    alternative = {"text": "MFG 08/2026 MFG 03/2026", "confidence": .91, "variant": "other"}
    result = extract([image("disputed.png", text, confidence=.54,
                            review_required=True, alternatives=[alternative])])
    values = result.intelligence["fields"]["manufacturing_date"]
    assert len(values) == 2
    for item in values:
        assert item["status"] == "needs_review" and item["ocr_confidence"] == .54
        assert item["ocr_alternatives"] == [alternative]
        assert item["sources"][0]["text"] == text
        assert item["sources"][0]["frame"] == "disputed.png"
        assert item["region_text"] == text
