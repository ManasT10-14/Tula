"""Pixel triggers and conservative handling of reconstructed numeric ink."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from tula.ocr.base import OcrLine
from tula.ocr.currency import price_regions
from tula.ocr.engines import RapidOcrEngine
from tula.ocr.preprocess import map_polygon, reconnect_ink_regions


def numeric_row(*, dotted=False, text="LOT ZK7981", shift=0):
    image = np.full((180, 800, 3), 245, dtype=np.uint8)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.putText(mask, text, (40 + shift, 105), cv2.FONT_HERSHEY_DUPLEX, 1.4, 255, 3)
    if dotted:
        sparse = np.zeros_like(mask)
        for y in range(50, 120, 3):
            for x in range(35 + shift, 700, 3):
                if mask[y, x] > 100:
                    cv2.circle(sparse, (x, y), 1, 255, -1)
        mask = sparse
    image[mask > 0] = 15
    yy, xx = np.where(mask > 0)
    line = OcrLine(text, (int(xx.min()) - 1, int(yy.min()) - 1,
                          int(xx.max()) + 2, int(yy.max()) + 2), .8)
    return image, line


@pytest.mark.parametrize("text", ["LOT ZK7981", "PACKED 11/2028", "PRICE 43.70"])
@pytest.mark.parametrize("shift", [0, 81])
def test_touching_dots_trigger_from_pixels_without_expected_values(text, shift):
    image, line = numeric_row(dotted=True, text=text, shift=shift)
    original = image.copy()
    regions = reconnect_ink_regions(image, [line])
    assert len(regions) == 1 and regions[0]["gap_fill_fraction"] >= .25
    assert regions[0]["bbox"] == list(line.bbox)
    # Changing the OCR text to a different number cannot supply pixel evidence.
    line.text = "UNKNOWN 9977"
    assert reconnect_ink_regions(image, [line]) == regions
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize("kind", ["clean", "blank", "solid", "faint", "noise"])
def test_clean_or_uninformative_pixels_do_not_trigger_reconnection(kind):
    image, line = numeric_row()
    if kind == "blank":
        image[:] = 245
    elif kind == "solid":
        image[:] = 15
    elif kind == "faint":
        image = np.where(image < 100, 220, 245).astype(np.uint8)
    elif kind == "noise":
        image = np.clip(180 + np.random.default_rng(994).normal(0, 3, image.shape), 0, 255).astype(np.uint8)
    assert reconnect_ink_regions(image, [line]) == []


def test_reconnect_measurements_are_bounded_and_require_detected_numeric_text():
    image, line = numeric_row(dotted=True)
    assert len(reconnect_ink_regions(image, [line] * 40)) == 16
    assert reconnect_ink_regions(image, []) == []
    line.text = "LOT WORDS"
    assert reconnect_ink_regions(image, [line]) == []


@pytest.mark.parametrize("secondary", [None, "BFG082026", "BFG 08/2026"])
def test_fragmented_numbers_need_actual_independent_literal_agreement(tmp_path, monkeypatch, secondary):
    # Deliberately malformed cue: protecting only a correctly recognized MFG
    # prefix would silently trust the very corruption this image causes.
    from tula.ocr import auxiliary

    image = np.full((400, 800, 3), 245, dtype=np.uint8)
    for y in range(120, 220, 5):
        for x in range(60, 700, 5):
            cv2.circle(image, (x, y), 1, (0, 0, 0), -1)
    path = tmp_path / "numeric.png"
    cv2.imwrite(str(path), image)
    box = (60, 120, 500, 220)
    engine = RapidOcrEngine()

    def reader(array):
        sx, sy = array.shape[1] / 800, array.shape[0] / 400
        polygon = [[box[0]*sx, box[1]*sy], [box[2]*sx, box[1]*sy],
                   [box[2]*sx, box[3]*sy], [box[0]*sx, box[3]*sy]]
        return ([[polygon, "BFG 08/2026", .91]], 0)

    engine._reader = reader
    monkeypatch.setenv("TULA_OCR_MAX_PASSES", "4")
    monkeypatch.setattr(auxiliary, "read_tesseract", lambda *a, **kw:
        ([OcrLine(secondary, box, .88, variants=["tesseract"])] if secondary else [], "eng", []))
    result = engine.read(str(path))
    chosen = next(line for line in result.lines if line.text == "BFG 08/2026")
    assert chosen.review_required is (secondary != "BFG 08/2026")
    assert chosen.confidence == (.91 if secondary == "BFG 08/2026" else .54)
    assert len(result.passes) <= 4
    assert any(candidate["confidence"] == .91 for candidate in chosen.alternatives)


def test_actual_price_crop_retains_punctuation_beyond_tall_digit_detector_box():
    path = Path(__file__).resolve().parents[1] / "data/uploads/1cfd866ae90e/IN-front-8901719134852.jpg"
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    line = OcrLine("101-", (1807, 136, 2112, 314), .84)
    _, variant, hint = next(price_regions(image, [line]))
    endpoints = map_polygon([(0, 0), (variant.image.shape[1], variant.image.shape[0])],
                            variant.to_original, image.shape[1], image.shape[0])
    assert endpoints[1][0] > 2113  # Existing source dash extends beyond x=2112.
    # A new source margin does not itself reinterpret the malformed model text.
    assert line.text == "101-" and hint["amount_text"] is None
    assert hint["requires_review"] and hint["currency_verified"] is False
