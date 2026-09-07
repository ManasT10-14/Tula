"""Source-supported currency context stays separate from literal OCR and MRP."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from tula.domain.enums import Panel
from tula.extract.layout import price_review_candidates
from tula.ocr.base import OcrLine
from tula.ocr.currency import attach_price_hint, currency_shape, price_regions
from tula.ocr.preprocess import map_polygon, reconnect_ink_variant

ROOT = Path(__file__).resolve().parents[1]
PHOTOS = [
    ("data/uploads/1ec337be56d7/IN-front-8901491366052.jpg", "20", (60, 36, 239, 134)),
    ("data/uploads/1cfd866ae90e/IN-front-8901719134852.jpg", "107-", (1783, 106, 2135, 351)),
]


@pytest.mark.parametrize(("path", "text", "box"), PHOTOS)
def test_actual_currency_ink_is_located_without_rewriting_ocr(path, text, box):
    image = cv2.imdecode(np.fromfile(ROOT / path, dtype=np.uint8), cv2.IMREAD_COLOR)
    original = image.copy()
    line = OcrLine(text, box, confidence=.9, frame=path)
    proposals = list(price_regions(image, [line]))
    assert len(proposals) == 1
    anchor, variant, hint = proposals[0]
    assert anchor is line and line.text == text
    assert hint["requires_review"] and hint["currency_verified"] is False
    assert hint["symbol_similarity"] >= .75
    assert hint["symbol_similarity"] - hint["negative_similarity"] >= .08
    assert hint["amount_text"] == ("20" if text == "20" else None)
    x0, y0, x1, y1 = hint["symbol_bbox"]
    assert 0 <= x0 < x1 <= image.shape[1] and 0 <= y0 < y1 <= image.shape[0]
    mapped = map_polygon([(0, 0), (variant.image.shape[1], variant.image.shape[0])],
                         variant.to_original, image.shape[1], image.shape[0])
    assert all(0 <= x <= image.shape[1] and 0 <= y <= image.shape[0] for x, y in mapped)
    assert max(variant.image.shape[:2]) <= 1600
    np.testing.assert_array_equal(image, original)


def test_removing_actual_currency_ink_removes_the_price_hypothesis():
    path, text, box = PHOTOS[0]
    image = cv2.imdecode(np.fromfile(ROOT / path, dtype=np.uint8), cv2.IMREAD_COLOR)
    line = OcrLine(text, box, confidence=.99)
    hint = next(price_regions(image, [line]))[2]
    x0, y0, x1, y1 = hint["symbol_bbox"]
    image[y0 - 2:y1 + 2, x0 - 2:x1 + 2] = (220, 220, 220)
    assert list(price_regions(image, [line])) == []


@pytest.mark.parametrize("text", ["12.5%", "20 g", "2026/03", "AB20", "MFG 20", "1800 200 1234"])
def test_unrelated_numeric_contexts_do_not_trigger_currency_probe(text):
    path, _, box = PHOTOS[0]
    image = cv2.imdecode(np.fromfile(ROOT / path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert list(price_regions(image, [OcrLine(text, box, confidence=.99)])) == []


@pytest.mark.parametrize("text", ["R", "F", "E", "Z", "S", "2", "3", "#", "x", "k"])
def test_competing_unseen_hershey_glyphs_do_not_become_rupee_symbols(text):
    ink = np.zeros((180, 180), np.uint8)
    cv2.putText(ink, text, (25, 125), cv2.FONT_HERSHEY_SIMPLEX, 3, 255, 7)
    assert currency_shape(ink) is None


def test_no_amount_is_inferred_from_a_corrupted_slash_reading():
    line = OcrLine("107-", (100, 20, 300, 150), confidence=.54)
    original = line.text, line.confidence
    attach_price_hint(line, {"requires_review": True, "amount_text": None, "readings": []})
    assert (line.text, line.confidence) == original
    assert price_review_candidates([(Panel.PDP, line)]) == []


def test_only_recorded_reading_becomes_an_explicit_review_candidate():
    line = OcrLine("107-", (100, 20, 300, 150), confidence=.54, frame="photo.jpg",
                   alternatives=[{"text": "101-", "confidence": .84}, {"text": "=10/-", "confidence": .72}])
    hint = {"requires_review": True, "symbol_bbox": [90, 40, 130, 90],
            "symbol_similarity": .88, "negative_similarity": .68,
            "readings": [{"amount_text": "10", "text": "=10/-", "bbox": [100, 20, 300, 150],
                          "confidence": .72, "variant": "tesseract_price_context_1"}]}
    attach_price_hint(line, hint)
    fields = price_review_candidates([(Panel.PDP, line), (Panel.PDP, line)])
    assert len(fields) == 1
    field = fields[0]
    assert field["value"] == {"value": 10., "currency": "INR", "currency_verified": False}
    assert field["status"] == "needs_review" and field["extraction_confidence"] is None
    assert field["raw"] == "=10/-" and field["sources"][0]["text"] == "=10/-"
    assert field["ocr_alternatives"][0]["text"] == "101-"
    assert line.text == "107-"  # Original model choice remains available.


def test_plain_number_without_pixel_hint_never_becomes_price():
    assert price_review_candidates([(Panel.PDP, OcrLine("20", (10, 10, 100, 50), .99))]) == []


def test_reconnect_variant_preserves_original_and_coordinates():
    image = np.full((720, 1100, 3), 247, dtype=np.uint8)
    for y in range(220, 265, 3):
        for x in range(60, 420, 3):
            cv2.circle(image, (x, y), 1, (15, 15, 15), -1)
    original = image.copy()
    variant = reconnect_ink_variant(image)
    assert variant.name == "ink_reconnect"
    assert max(variant.image.shape[:2]) <= 1800
    corners = map_polygon([(0, 0), (variant.image.shape[1], variant.image.shape[0])],
                          variant.to_original, 1100, 720)
    np.testing.assert_allclose(corners, [(0, 0), (1100, 720)], atol=.01)
    np.testing.assert_array_equal(image, original)
