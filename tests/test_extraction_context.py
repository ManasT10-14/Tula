"""Adversarial semantic/layout tests using injected OCR, not image accuracy claims."""
from itertools import permutations

import pytest

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Panel
from tula.extract import normalizers
from tula.extract.pipeline import extract
from tula.ocr.base import OcrLine, OcrResult


def line(text, box=(20, 20, 260, 50), *, score=.98, height=None, angle=0, frame="label.png"):
    return OcrLine(text, box, confidence=score, height_px=height, angle_degrees=angle, frame=frame)


def run(*lines, quality=None):
    return extract([(Panel.PDP, OcrResult(lines=list(lines), width=1200, height=1600, quality=quality or {}))])


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_marketing_context_follows_geometry_not_ocr_list_order(order):
    lines = [line("MADEWITH", (20, 100, 220, 130)),
             line("QUALITY SPICES", (20, 133, 240, 163)),
             line("CLEARVIEW", (700, 100, 980, 130))]
    result = run(*(lines[index] for index in order))
    assert DC.GENERIC_NAME not in result.declarations
    brand = result.declarations[DC.BRAND]
    assert brand.norm["name"] is None and brand.norm["candidates"] == [{"name": "clearview"}]
    assert brand.provenance.status == "needs_review" and brand.raw == "CLEARVIEW"


@pytest.mark.parametrize("text,expected", [("Chocolate Biscuits", "biscuits"),
    ("Milk Chocolate", "chocolate"), ("Masala Noodles", "noodles"), ("Mustard Oil", "mustard oil"),
    ("Biscuit with chocolate", "biscuit"), ("Noodles made with spices", "noodles")])
def test_commodity_head_is_retained_without_flavour_modifiers(text, expected):
    declaration = run(line(text)).declarations[DC.GENERIC_NAME]
    assert declaration.norm["name"] == expected
    assert declaration.raw == text and declaration.provenance.sources[0].text == text


def test_flavour_on_its_own_line_does_not_outrank_commodity():
    result = run(line("CREME", (20, 20, 240, 50)), line("BISCUIT", (20, 53, 240, 83)),
                 line("WITH", (20, 86, 160, 116)), line("CHOCOLATE", (20, 119, 300, 149)))
    assert result.declarations[DC.GENERIC_NAME].norm["name"] == "biscuit"


def test_puffcorn_is_a_supported_commodity_not_its_cheese_flavour():
    result = run(line("PUFFCORN", (20, 20, 700, 150)), line("YUMMY CHEESE", (20, 155, 480, 195)))
    assert result.declarations[DC.GENERIC_NAME].norm["name"] == "puffcorn"


@pytest.mark.parametrize("cue", ["Ingredients:", "Contains:", "TAINS:", "Made with"])
def test_ingredient_or_claim_regions_never_become_commodity(cue):
    result = run(line(cue, (20, 20, 180, 45)), line("Wheat, Milk", (20, 48, 230, 73)))
    assert DC.GENERIC_NAME not in result.declarations


def test_distant_claim_does_not_suppress_readable_standalone_food():
    result = run(line("Made with", (800, 20, 1000, 50)), line("Milk", (20, 20, 200, 50)))
    assert result.declarations[DC.GENERIC_NAME].norm["name"] == "milk"


@pytest.mark.parametrize("nutrient", ["Sodium", "Protein", "Energy", "Calcium", "Nutrition information", "Serving size"])
def test_split_nutrition_cells_are_not_net_contents(nutrient):
    result = run(line(nutrient, (20, 20, 200, 45)), line("700 mg", (20, 48, 120, 73)))
    assert DC.NET_QUANTITY not in result.declarations


def test_net_cue_cannot_borrow_a_neighboring_nutrition_value():
    result = run(line("Net quantity", (20, 20, 200, 45)),
                 line("Sodium", (230, 20, 320, 45)), line("700 mg", (230, 48, 320, 73)))
    assert result.declarations[DC.NET_QUANTITY].norm == {}
    assert result.declarations[DC.NET_QUANTITY].provenance.status == "cue_only"


def test_true_net_quantity_remains_available_beside_nutrition():
    result = run(line("Sodium", (20, 20, 160, 45)), line("700 mg", (20, 48, 160, 73)),
                 line("Net quantity: 250 g", (500, 20, 830, 55)))
    declaration = result.declarations[DC.NET_QUANTITY]
    assert declaration.norm["value_base"] == 250
    assert all("700" not in source.text for source in declaration.provenance.sources)


def test_uncued_standalone_quantity_still_works_without_nutrition_context():
    assert run(line("500 g")).declarations[DC.NET_QUANTITY].norm["value_base"] == 500


def test_nutrition_on_other_frame_does_not_poison_standalone_quantity():
    result = run(line("Sodium", frame="back.png"), line("250 g", (20, 55, 200, 80), frame="front.png"))
    assert result.declarations[DC.NET_QUANTITY].norm["value_base"] == 250


@pytest.mark.parametrize("angle,boxes", [(0, [(20, 20, 360, 100), (20, 103, 360, 183)]),
    (90, [(150, 20, 230, 360), (67, 20, 147, 360)])])
def test_complete_multiline_names_use_reading_axes_and_all_sources(angle, boxes):
    result = run(line("SILVER", boxes[0], height=65, angle=angle, score=.97),
                 line("MOON", boxes[1], height=65, angle=angle, score=.91))
    declaration = result.declarations[DC.BRAND]
    assert declaration.norm["name"] is None
    assert declaration.norm["candidates"] == [{"name": "silver moon"}]
    assert [source.text for source in declaration.provenance.sources] == ["SILVER", "MOON"]
    assert declaration.provenance.ocr_confidence == .91
    assert declaration.provenance.status == "needs_review"


@pytest.mark.parametrize("second", [line("MOON", (850, 20, 1160, 100), height=65),
    line("MOON", (20, 103, 360, 183), height=65, frame="back.png"),
    line("MOON", (20, 103, 180, 123), height=16)])
def test_unrelated_names_are_not_concatenated(second):
    declaration = run(line("SILVER", (20, 20, 360, 100), height=65), second).declarations[DC.BRAND]
    assert declaration.norm["name"] is None
    assert declaration.norm["candidates"][0]["name"] in {"silver", "moon"}
    assert declaration.provenance.status == "needs_review"
    assert len(declaration.provenance.sources) == 1


def test_small_readable_name_beats_large_promotion_and_clipped_commodity():
    result = run(line("CEDAR", (20, 20, 260, 55), height=25),
                 line("YOUR FAVOURITE", (20, 300, 850, 430), height=100),
                 line("masala taste", (20, 435, 850, 565), height=100),
                 line("oodles", (20, 140, 500, 225), height=65),
                 line("Minute", (20, 80, 500, 138), height=50))
    assert result.declarations[DC.BRAND].norm["name"] is None
    assert result.declarations[DC.BRAND].norm["candidates"] == [{"name": "cedar"}]
    assert result.declarations[DC.BRAND].provenance.status == "needs_review"
    assert DC.GENERIC_NAME not in result.declarations


def test_explicit_identity_cue_can_name_a_brand_using_promotional_words():
    result = run(line("Brand: New Light", (20, 20, 300, 50)),
                 line("SHOUT", (20, 200, 950, 400), height=160),
                 line("Store in a dry place", (20, 500, 500, 530)),
                 line("Dispose as dry waste", (20, 550, 500, 580)))
    declaration = result.declarations[DC.BRAND]
    assert declaration.norm["name"] == "new light"
    assert declaration.raw == "Brand: New Light"
    assert declaration.provenance.method == "explicit_brand_cue"
    assert declaration.provenance.status == "detected"


def test_food_word_explicitly_used_as_brand_is_not_a_commodity():
    result = run(line("Brand: Tea"))
    assert result.declarations[DC.BRAND].norm["name"] == "tea"
    assert DC.GENERIC_NAME not in result.declarations


def test_footnoted_commodity_candidate_retains_text_but_withholds_identity():
    declaration = run(line("Masala*")).declarations[DC.GENERIC_NAME]
    assert declaration.norm["name"] is None
    assert declaration.norm["candidates"] == [{"name": "masala"}]
    assert declaration.provenance.status == "needs_review" and declaration.raw == "Masala*"


def test_degraded_identity_candidate_remains_reviewable_without_catalogue_name():
    declaration = run(line("CEDAR"), quality={"issues": [{"code": "blur_or_low_detail"}]}).declarations[DC.BRAND]
    assert declaration.norm["name"] is None and declaration.norm["candidates"] == [{"name": "cedar"}]
    assert declaration.provenance.status == "needs_review" and declaration.provenance.sources


@pytest.mark.parametrize("text", ["Original Gluco Biscuits", "The Original", "Originally made", "Originated here"])
def test_origin_cue_must_be_a_complete_word(text):
    assert normalizers.parse_country_of_origin(text) is None
    assert DC.COUNTRY_OF_ORIGIN not in run(line(text)).declarations


@pytest.mark.parametrize("cue", ["Origin:", "Country of origin:", "Made in", "Product of"])
def test_genuine_origin_wording_is_located_and_retained(cue):
    declaration = run(line(f"{cue} India")).declarations[DC.COUNTRY_OF_ORIGIN]
    assert declaration.norm["country"] == "India"
    assert declaration.provenance.sources[0].text == f"{cue} India"


def test_split_origin_uses_local_source_and_does_not_join_other_frame():
    result = run(line("Country of origin", (20, 20, 300, 50)), line("India", (20, 53, 200, 83)))
    declaration = result.declarations[DC.COUNTRY_OF_ORIGIN]
    assert declaration.norm["country"] == "India" and len(declaration.provenance.sources) == 2
    other = run(line("Country of origin", (20, 20, 300, 50)),
                line("India", (20, 53, 200, 83), frame="back.png"))
    assert other.declarations[DC.COUNTRY_OF_ORIGIN].norm == {}


def price_hint_line():
    result = line("107-", (20, 20, 160, 75), score=.54)
    result.price_hint = {"requires_review": True, "symbol_bbox": [5, 20, 20, 75],
        "symbol_similarity": .72, "negative_similarity": .40,
        "readings": [{"amount_text": "10", "text": "10/-", "confidence": .91,
                      "bbox": [20, 20, 160, 75]}]}
    return result


def test_review_price_hints_survive_ocr_filter_without_becoming_primary_price():
    hinted = price_hint_line()
    result = run(hinted, hinted)
    assert DC.RETAIL_SALE_PRICE not in result.declarations
    items = result.intelligence["fields"]["retail_sale_price"]
    assert len(items) == 1
    assert items[0]["value"] == {"value": 10, "currency": "INR", "currency_verified": False}
    assert items[0]["status"] == "needs_review" and items[0]["extraction_confidence"] is None
    assert items[0]["raw"] == "10/-" and items[0]["sources"][0]["bbox"] == [20, 20, 160, 75]
    assert items[0]["currency_evidence"]["symbol_bbox"] == [5, 20, 20, 75]


def test_review_price_is_not_aggregated_with_an_accepted_printed_mrp():
    result = run(price_hint_line(), line("MRP Rs 200 inclusive of all taxes", (20, 300, 650, 345)))
    primary = result.declarations[DC.RETAIL_SALE_PRICE]
    assert primary.norm["value"] == 200 and primary.norm["count"] == 1
    assert all("10/-" not in source.text for source in primary.provenance.sources)
    assert any(item["value"]["value"] == 10 and item["status"] == "needs_review"
               for item in result.intelligence["fields"]["retail_sale_price"])
