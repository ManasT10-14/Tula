"""Evidence and uncertainty regressions for real label layout failure modes."""
import json
from datetime import date

import pytest

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Panel
from tula.extract import intelligence, normalizers
from tula.extract.intelligence import analyze_allergens, date_candidates, extract_intelligence
from tula.extract.pipeline import extract
from tula.ocr.base import OcrLine, OcrResult


def line(text, bbox=(0, 0, 180, 20), *, confidence=0.98, frame="front.png", **attrs):
    result = OcrLine(text, bbox, confidence=confidence, frame=frame)
    for key, value in attrs.items():
        setattr(result, key, value)
    return result


def run(*lines):
    return extract([(Panel.PDP, OcrResult(lines=list(lines)))])


@pytest.mark.parametrize("bbox", [(100, 3, 165, 23), (0, 30, 75, 50), (95, 13, 155, 33)])
def test_split_mrp_associates_nearby_bare_value(bbox):
    result = run(line("MRP", (0, 0, 60, 20)), line("120", bbox))
    declaration = result.declarations[DC.RETAIL_SALE_PRICE]
    assert declaration.norm["value"] == 120
    assert declaration.bbox == bbox
    assert len(declaration.norm["source_spans"]) == 2
    assert declaration.norm["extraction_method"] == "spatial_keyword_value"


def test_vertical_mrp_association_uses_text_axis():
    result = run(line("MRP", (20, 10, 40, 70), angle_degrees=90),
                 line("120", (18, 85, 38, 140), angle_degrees=90))
    assert result.declarations[DC.RETAIL_SALE_PRICE].norm["value"] == 120


def test_split_quantity_retains_keyword_and_value_boxes():
    result = run(line("Net quantity", (0, 0, 150, 20)), line("500 g", (160, 7, 225, 27)))
    value = result.declarations[DC.NET_QUANTITY]
    assert value.norm["value"] == 500
    assert len(value.norm["source_spans"]) == 2


def test_bare_number_on_another_frame_is_never_joined_to_mrp():
    result = run(line("MRP", frame="front.png"), line("120", (0, 24, 100, 44), frame="back.png"))
    assert not result.declarations[DC.RETAIL_SALE_PRICE].norm


def test_same_unnamed_panel_across_captures_is_not_one_surface():
    result = extract([(Panel.PDP, OcrResult(lines=[line("MRP", frame=None)])),
                      (Panel.PDP, OcrResult(lines=[line("120", (0, 25, 100, 45), frame=None)]))])
    assert not result.declarations[DC.RETAIL_SALE_PRICE].norm


def test_split_mrp_does_not_take_distant_number_or_batch():
    result = run(line("MRP", (0, 0, 55, 20)), line("120", (700, 0, 760, 20)),
                 line("Batch 456", (0, 25, 100, 45)))
    assert not result.declarations[DC.RETAIL_SALE_PRICE].norm


def test_manufacturer_does_not_steal_contact_from_far_column():
    result = run(line("Manufactured by ABC Ltd", (0, 0, 240, 20)),
                 line("Customer care", (600, 0, 750, 20)),
                 line("Plot 22, Pune 411018", (600, 30, 820, 50)))
    assert not result.declarations[DC.MANUFACTURER].norm["has_address"]


def test_manufacturer_multiline_address_is_spatial_not_ocr_order():
    result = run(line("Manufactured by ABC Ltd", (0, 0, 240, 20)),
                 line("MRP 120", (700, 100, 790, 120)),
                 line("Plot 22, Pune 411018", (0, 27, 230, 47)))
    assert result.declarations[DC.MANUFACTURER].norm["has_address"]


@pytest.mark.parametrize("text,expected", [
    ("2026-09-08", ["2026-09-08"]),
    ("15/03/2026", ["2026-03-15"]),
    ("08/09/26", ["2026-09-08", "2026-08-09"]),
    ("MAR 2026", ["2026-03"]),
    ("MAR2026", ["2026-03"]),
    ("15SEP2026", ["2026-09-15"]),
    ("15 Sep 2026", ["2026-09-15"]),
    ("03/2026", ["2026-03"]),
    ("2026/03", ["2026-03"]),
    ("31/02/2026", []),
])
def test_date_candidates_preserve_ambiguity_and_calendar_validity(text, expected):
    parsed = date_candidates(text)
    assert [candidate["iso"] for candidate in parsed["candidates"]] == expected
    if len(expected) != 1:
        assert parsed["status"] == "needs_review"


def test_explicit_printed_format_resolves_day_month_order():
    parsed = date_candidates("DD/MM/YYYY 08/09/2026")
    assert [c["iso"] for c in parsed["candidates"]] == ["2026-09-08"]


def test_ambiguous_packing_date_cannot_silently_select_rule_version():
    assert normalizers.parse_date("Mfg 08/09/2026") is None
    result = run(line("Mfg 08/09/26"))
    value = result.intelligence["fields"]["manufacturing_date"][0]
    assert value["value"] is None and value["status"] == "needs_review"
    assert len(value["candidates"]) == 2


def test_mfg_expiry_and_batch_same_line_are_separate_fields():
    result = run(line("MFG 15/03/2026 EXP 15/09/2026 Batch: AB52"))
    fields = result.intelligence["fields"]
    assert fields["manufacturing_date"][0]["value"] == "2026-03-15"
    assert fields["expiry_date"][0]["value"] == "2026-09-15"
    assert fields["batch_number"][0]["value"] == "AB52"


def test_best_before_duration_is_not_invented_expiry_date():
    result = run(line("Best before 12 months from manufacture"))
    value = result.intelligence["fields"]["best_before"][0]
    assert value["value"] == {"duration": 12, "unit": "month", "reference": "manufacturing_date"}
    assert value["status"] == "relative_duration"
    assert "expiry_date" not in result.intelligence["fields"]


def test_no_temporal_claim_from_ambiguous_date():
    data = extract_intelligence([(Panel.PDP, line("EXP 08/09/26"))], as_of=date(2026, 9, 9))
    assert "temporal_status" not in data["fields"]["expiry_date"][0]


def test_low_confidence_and_ocr_disagreement_remain_visible():
    result = run(line("MRP 180", confidence=0.54, review_required=True,
                      alternatives=[{"text": "MRP 160", "confidence": .91, "variant": "inkjet"}]))
    assert DC.RETAIL_SALE_PRICE not in result.declarations
    field = result.intelligence["fields"]["retail_sale_price"][0]
    assert field["status"] == "needs_review"
    assert field["candidates"][0]["value"]["value"] == 160
    assert field["sources"][0]["bbox"] == [0, 0, 180, 20]
    assert "conflicting" in result.warnings[0]


def test_ingredients_span_multiple_lines_but_stop_at_other_sections():
    result = run(line("Ingredients: Wheat flour,", (0, 0, 250, 20)),
                 line("milk solids, peanut oil, INS 322", (0, 27, 300, 47)),
                 line("MRP 120", (0, 56, 150, 76)))
    ingredients = result.intelligence["fields"]["ingredients"]
    assert "milk solids" in ingredients[0]["value"]
    assert "MRP" not in ingredients[0]["value"]
    assert len(ingredients[0]["sources"]) == 2
    assert result.intelligence["additives"][0]["code"] == "INS 322"


def test_allergens_distinguish_explicit_possible_and_cross_contact():
    result = extract([(Panel.BACK, OcrResult(lines=[
        line("Ingredients: Milk solids, wheat flour, casein, peanut oil", (0, 0, 500, 20)),
        line("May contain soy", (0, 30, 180, 50)),
    ]))], allergen_concerns=["milk", "gluten", "peanuts", "soy"])
    matches = result.intelligence["allergens"]["matches"]
    assert any(m["concern"] == "milk" and m["kind"] == "explicit" for m in matches)
    assert any(m["concern"] == "milk" and m["kind"] == "possible" for m in matches)
    assert any(m["concern"] == "gluten" and m["kind"] == "possible" for m in matches)
    assert any(m["concern"] == "soy" and m["kind"] == "cross_contact" for m in matches)
    assert all(m["sources"] and m["matched_text"] for m in matches)


def test_negative_claims_and_partial_words_do_not_create_allergen_matches():
    fields = [{"value": "Milk-free, no peanuts, soybean-free, buttermilkweed", "raw": "label"}]
    assert not analyze_allergens(fields, ["milk", "peanuts", "soy"])["matches"]


def test_custom_allergen_is_literal_and_missing_match_is_not_safety_claim():
    fields = [{"value": "Ingredients: Lupin flour", "raw": "Ingredients: Lupin flour"}]
    result = analyze_allergens(fields, ["lupin", "milk", "(.*)"])
    assert [m["concern"] for m in result["matches"]] == ["lupin"]
    assert result["unmatched"] == ["milk", "(.*)"]
    assert "does not establish absence" in result["disclaimer"]


def test_plant_milk_and_cocoa_butter_are_not_milk_evidence():
    result = analyze_allergens([{"value": "Coconut milk, almond milk, cocoa butter, peanut butter"}], ["milk"])
    assert not result["matches"]


def test_contains_and_may_contain_on_one_line_keep_their_distinct_meaning():
    result = analyze_allergens([{"value": "Contains milk. May contain peanuts"}], ["milk", "peanuts"])
    assert {(item["concern"], item["kind"]) for item in result["matches"]} == {
        ("milk", "explicit"), ("peanuts", "cross_contact")}


def test_product_category_uses_product_name_not_ingredients():
    result = run(line("Milk shampoo", (0, 0, 180, 20)),
                 line("Ingredients: milk extract", (0, 35, 250, 55)))
    assert result.intelligence["product"]["category"] == "unknown"
    unknown = run(line("Ingredients: wheat, milk, sugar"))
    assert unknown.intelligence["product"]["food_nonfood"] == "unknown"


def test_domestic_country_cue_is_not_evidence_of_import():
    assert not normalizers.looks_imported("Country of origin: India")
    result = run(line("Country of origin: India"))
    assert not result.is_imported
    assert result.intelligence["product"]["origin"] == "domestic"


def test_manufacturer_identity_is_not_a_packing_date_cue():
    result = run(line("Manufactured by ABC Foods Pvt Ltd"))
    assert DC.MANUFACTURER in result.declarations
    assert DC.DATE_OF_PACKING not in result.declarations


def test_shared_line_origin_stops_at_mrp_heading():
    assert normalizers.parse_country_of_origin("Made in India MRP 120")["country"] == "India"


def test_same_ingredient_across_images_preserves_all_sources_without_duplicate_match():
    result = extract([(Panel.PDP, OcrResult(lines=[line("Ingredients: milk", frame="a.png")])),
                      (Panel.BACK, OcrResult(lines=[line("Ingredients: milk", frame="b.png")]))],
                     allergen_concerns=["milk"])
    ingredients = result.intelligence["fields"]["ingredients"]
    assert len(ingredients) == 1 and len(ingredients[0]["sources"]) == 2
    assert len(result.intelligence["allergens"]["matches"]) == 1
    json.dumps(result.intelligence)  # Public output must be storable/API safe.


def test_two_values_beside_one_cue_are_uncertain_not_established_dual_mrp():
    result = run(line("MRP", (0, 0, 60, 20)),
                 line("120", (100, 0, 140, 20)), line("180", (100, 21, 140, 41)))
    mrp = result.declarations[DC.RETAIL_SALE_PRICE].norm
    assert mrp["requires_review"] and mrp["value"] is None and mrp["count"] is None
    assert {candidate["value"] for candidate in mrp["candidates"]} == {120, 180}


@pytest.mark.parametrize("request_context", ["absent", "without_state", "without_user", "anonymous"])
def test_intelligence_template_renders_real_crops_and_escaped_ocr(request_context):
    from pathlib import Path
    from types import SimpleNamespace

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    result = extract([(Panel.PDP, OcrResult(lines=[
        line("Ingredients: Milk solids <script>alert(1)</script>"),
        line("EXP 08/09/26", (0, 30, 300, 50)),
    ], width=640, height=480))], allergen_concerns=["milk"])
    a = SimpleNamespace(intelligence=result.intelligence,
                        scan=SimpleNamespace(scan_id="INS001", frames=["front.png"]),
                        review=SimpleNamespace(revision=2))
    environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "src/tula/web/templates"),
                              autoescape=select_autoescape(["html"]))
    context = {}
    if request_context == "without_state":
        context["request"] = SimpleNamespace()
    elif request_context == "without_user":
        context["request"] = SimpleNamespace(state=SimpleNamespace())
    elif request_context == "anonymous":
        context["request"] = SimpleNamespace(state=SimpleNamespace(user=None))
    output = environment.get_template("_intelligence.html").render(a=a, csrf_token="test-csrf", **context)
    assert 'viewBox="' in output and 'href="/inspections/INS001/frames/0"' in output
    assert "<form" not in output and "Correct this observation" not in output
    assert "Manual verification required" in output and "2026-08-09" in output
    assert "<script>alert(1)</script>" not in output
    assert "&lt;script&gt;" in output


# ---------------------------------------------------------------------------
# Unprompted allergen screening
# ---------------------------------------------------------------------------


def _ingredient(value, method="ingredient_block"):
    return {"value": value, "raw": value, "sources": [], "method": method,
            "ocr_confidence": 0.9, "extraction_confidence": 0.87, "status": "detected"}


def test_screen_names_allergens_nobody_asked_about():
    # The officer-directed search answers "is milk in this?". This answers the
    # question they actually have: "what is in this?"
    screen = intelligence.screen_allergens([
        _ingredient("Wheat flour, sugar, milk solids, cashew paste, soya lecithin, salt."),
    ])
    found = {item["allergen"]: item["kind"] for item in screen["detected"]}
    assert found["milk"] == "explicit"
    assert found["tree nuts"] == "explicit"
    assert found["soy"] == "explicit"
    assert found["gluten"] == "possible"  # wheat implies it; the word is absent


def test_screen_separates_the_ingredients_from_the_contains_statement():
    screen = intelligence.screen_allergens([
        _ingredient("Wheat flour, milk solids, cashew paste, soya lecithin."),
        _ingredient("Contains milk. May contain traces of peanuts.", "allergen_statement"),
    ])
    by_name = {item["allergen"]: item for item in screen["detected"]}
    assert by_name["milk"]["declared_in_statement"]
    assert by_name["peanuts"]["kind"] == "cross_contact"
    # Named in the ingredients, absent from the statement: the pattern worth
    # an officer's attention. "Possible" matches never qualify.
    assert screen["undeclared"] == ["soy", "tree nuts"]


def test_screen_reports_nothing_for_a_label_that_names_nothing():
    screen = intelligence.screen_allergens([
        _ingredient("Water, sugar, citric acid (INS 330), permitted natural colour."),
    ])
    assert screen["detected"] == []
    assert screen["undeclared"] == []
    assert screen["referral"] == ""


def test_screen_does_not_read_a_denial_as_a_declaration():
    screen = intelligence.screen_allergens([
        _ingredient("Contains no milk. Coconut milk, cocoa butter, peanut-free facility."),
    ])
    assert "milk" not in {item["allergen"] for item in screen["detected"]}


def test_screen_is_reachable_from_the_recorded_analysis():
    # It rides on the same structure the officer's own concern list uses, so
    # every caller that stores or re-runs allergen analysis carries it too.
    result = intelligence.analyze_allergens([_ingredient("Milk solids, wheat flour.")], ())
    assert result["concerns"] == []
    assert {item["allergen"] for item in result["screen"]["detected"]} == {"milk", "gluten"}
    assert "regulation 5(3)" in result["screen"]["basis"]
    assert "not a Legal Metrology" in result["screen"]["referral"]


# ---------------------------------------------------------------------------
# Hindi labels, which the Rules put on an equal footing with English
# ---------------------------------------------------------------------------


def test_screen_reads_a_devanagari_ingredient_list():
    # Rule 6 permits the declaration in Hindi in Devanagari or in English. A
    # lexicon that reads only English reads only half the labels it is shown.
    screen = intelligence.screen_allergens([
        _ingredient("गेहूं का आटा, दूध ठोस, काजू, चीनी, नमक"),
    ])
    found = {item["allergen"]: item["kind"] for item in screen["detected"]}
    assert found["milk"] == "explicit"        # दूध
    assert found["tree nuts"] == "explicit"   # काजू
    assert found["gluten"] == "possible"      # गेहूं
    assert screen["undeclared"] == ["milk", "tree nuts"]


def test_hindi_cross_contact_reads_after_the_allergen_not_before():
    # English qualifies before the noun and Hindi after it, so the clause has
    # to be read on both sides of the match.
    screen = intelligence.screen_allergens([
        _ingredient("गेहूं का आटा, दूध ठोस"),
        _ingredient("इसमें दूध शामिल है। मूंगफली के अंश हो सकते हैं।", "allergen_statement"),
    ])
    kinds = {item["allergen"]: item["kind"] for item in screen["detected"]}
    assert kinds["milk"] == "explicit"
    assert kinds["peanuts"] == "cross_contact"
    # The danda ends the sentence; without it the cross-contact cue would leak
    # backwards and demote the milk declaration.
    assert any(i["allergen"] == "milk" and i["declared_in_statement"]
               for i in screen["detected"])


def test_hindi_free_from_claim_is_not_a_declaration():
    screen = intelligence.screen_allergens([
        _ingredient("ग्लूटेन रहित जई, दूध मुक्त, चीनी"),
    ])
    assert screen["detected"] == []


def test_english_facility_wordings_beyond_the_word_facility():
    for statement in (
        "Made in a factory that also handles peanuts",
        "Produced on shared equipment with sesame",
        "Manufactured in premises that also processes almonds",
    ):
        screen = intelligence.screen_allergens([
            _ingredient("Rice, salt"),
            _ingredient(statement, "allergen_statement"),
        ])
        assert screen["detected"], statement
        assert all(item["kind"] == "cross_contact" for item in screen["detected"]), statement
        # A cross-contact mention is never escalated into an undeclared
        # ingredient referral.
        assert screen["undeclared"] == []
