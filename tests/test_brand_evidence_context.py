"""Positive same-image identity evidence and explicit abstention tradeoffs."""
from dataclasses import asdict

import pytest

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Panel
from tula.extract.pipeline import extract
from tula.ocr.base import OcrLine, OcrResult


def line(text, box=(10, 10, 250, 60), *, frame="source.png", score=.96, **extra):
    return OcrLine(text, box, confidence=score, frame=frame, **extra)


def run(*lines, quality=None):
    return extract([(Panel.PDP, OcrResult(lines=list(lines), width=1000, height=900, quality=quality or {}))])


def held(result, name):
    brand = result.declarations[DC.BRAND]
    assert brand.norm["name"] is None and brand.norm["requires_review"]
    assert brand.norm["candidates"] == [{"name": name.lower()}]
    assert brand.provenance.status == "needs_review"
    assert result.intelligence["declarations"]["brand"]["value"]["candidates"] == [{"name": name.lower()}]
    return brand


@pytest.mark.parametrize("fragment", ["PRIOR", "ALTO", "RENOU", "MONAT"])
def test_unfamiliar_field_closeup_word_is_preserved_without_brand_acceptance(fragment):
    source = line(fragment)
    before = asdict(source)
    result = run(source, line("08/2026", (10, 100, 250, 135)),
                 line("LOT 8K9", (10, 150, 250, 185)), line("14:35", (10, 210, 250, 245)))
    brand = held(result, fragment)
    assert brand.raw == fragment and brand.provenance.ocr_confidence == .96
    assert brand.provenance.sources[0].bbox == source.bbox
    assert brand.provenance.sources[0].frame == "source.png"
    assert "same source image" in brand.norm["review_reason"]
    assert asdict(source) == before


def test_legitimate_logo_only_front_abstains_but_keeps_exact_multiline_name():
    result = run(line("SILVER", (10, 10, 250, 60)), line("MOON", (10, 63, 250, 113), score=.87))
    brand = held(result, "silver moon")
    assert brand.raw == "SILVER MOON"
    assert [source.text for source in brand.provenance.sources] == ["SILVER", "MOON"]
    assert brand.provenance.ocr_confidence == .87


def test_same_multiline_logo_is_accepted_with_readable_same_image_commodity():
    result = run(line("SILVER", (10, 10, 250, 60)), line("MOON", (10, 63, 250, 113), score=.87),
                 line("NOODLES", (10, 200, 250, 225), score=.91))
    brand = result.declarations[DC.BRAND]
    assert brand.norm["name"] == "silver moon" and brand.provenance.status == "detected"
    assert brand.provenance.ocr_confidence == .87
    assert [source.text for source in brand.provenance.sources] == ["SILVER", "MOON"]
    support = brand.norm["identity_support"]
    assert support["kind"] == "same_image_commodity" and support["commodity"] == "noodles"
    assert support["sources"][0]["text"] == "NOODLES"
    assert support["sources"][0]["ocr_confidence"] == .91


@pytest.mark.parametrize("cue", ["Brand:", "Brand name:", "Trade name -"])
def test_explicit_brand_label_does_not_require_a_commodity(cue):
    brand = run(line(cue + " New Light"), line("08/2026", (10, 200, 200, 230))).declarations[DC.BRAND]
    assert brand.norm["name"] == "new light" and brand.provenance.status == "detected"
    assert brand.provenance.method == "explicit_brand_cue"
    assert brand.norm["identity_support"]["kind"] == "explicit_brand_cue"


def test_a_commodity_on_another_frame_cannot_validate_the_name():
    result = run(line("ASTERA", frame="front.png"), line("NOODLES", (10, 200, 200, 230), frame="back.png"))
    assert result.declarations[DC.GENERIC_NAME].norm["name"] == "noodles"
    brand = held(result, "astera")
    assert "identity_support" not in brand.norm


@pytest.mark.parametrize("frame", [None, "reused-name.png"])
def test_same_panel_and_path_on_distinct_captures_cannot_lend_support(frame):
    result = extract([(Panel.PDP, OcrResult(lines=[line("ASTERA", frame=frame)])),
                      (Panel.PDP, OcrResult(lines=[line("NOODLES", frame=frame)]))])
    brand = result.declarations[DC.BRAND]
    assert brand.norm["name"] is None and brand.norm["candidates"] == [{"name": "astera"}]
    assert brand.provenance.status != "detected"
    assert brand.provenance.sources[0].image_index == 0


@pytest.mark.parametrize("commodity", [
    line("NOODLES", (10, 200, 250, 230), score=.4),
    line("NOODLES", (10, 200, 250, 230), review_required=True),
    line("NOODLES*", (10, 200, 250, 230)),
    line("Ingredients: Noodles, salt", (10, 200, 250, 230)),
    line("With noodles", (10, 200, 250, 230)),
    line("NOODLES \ufffd", (10, 200, 250, 230)),
])
def test_unreadable_disputed_footnoted_or_descriptive_commodity_is_not_identity_support(commodity):
    held(run(line("ASTERA"), commodity), "astera")


def test_valid_commodity_still_supports_identity_when_another_commodity_is_footnoted():
    brand = run(line("ASTERA"), line("Mustard Oil*", (10, 200, 250, 250)),
                line("NOODLES", (10, 350, 250, 380))).declarations[DC.BRAND]
    assert brand.norm["name"] == "astera"
    assert brand.norm["identity_support"]["commodity"] == "noodles"


def test_supported_front_name_outweighs_large_unsupported_closeup_fragment():
    result = run(line("ASTERA", frame="front.png"), line("NOODLES", (10, 200, 200, 230), frame="front.png"),
                 line("PRIOR", (10, 10, 800, 180), frame="stamp.png"))
    brand = result.declarations[DC.BRAND]
    assert brand.norm["name"] == "astera" and brand.frame == "front.png"
    assert brand.norm["identity_support"]["sources"][0]["frame"] == "front.png"


def test_resolution_warning_alone_is_not_a_blanket_identity_rejection():
    result = run(line("ASTERA"), line("NOODLES", (10, 200, 200, 230)),
                 quality={"issues": [{"code": "low_resolution"}, {"code": "text_near_edge"}]})
    assert result.declarations[DC.BRAND].norm["name"] == "astera"


def test_disputed_explicit_or_multiline_name_is_reviewed_without_increasing_scores():
    explicit = line("Brand: Astera", review_required=True, score=.94,
                    alternatives=[{"text": "Brand: Astero", "confidence": .95, "variant": "contrast"}])
    brand = held(run(explicit), "astera")
    assert brand.provenance.ocr_confidence == .94
    result = run(line("SILVER", (10, 10, 250, 60)),
                 line("MOON", (10, 63, 250, 113), review_required=True, score=.83),
                 line("NOODLES", (10, 200, 250, 230)))
    assert held(result, "silver moon").provenance.ocr_confidence == .83
