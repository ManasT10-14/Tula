"""Geometry, arbitration, real preprocessing and bounded CPU-work contracts."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from tula.imaging.quality import assess_quality
from tula.ocr.arbitrate import arbitrate
from tula.ocr.base import OcrLine
from tula.ocr.engines import RapidOcrEngine
from tula.ocr.preprocess import fit_variant, map_polygon, rotate_variant, targeted_variants


def line(text, variant, confidence=0.98, bbox=(100, 100, 400, 135)):
    return OcrLine(text, bbox, confidence=confidence, variants=[variant])


def test_conflicting_mrp_preserves_original_scores_but_blocks_trust():
    chosen, conflicts = arbitrate([line("MRP Rs 180", "original"), line("MRP Rs 160", "clahe")])
    assert len(chosen) == 1 and len(conflicts) == 1
    assert chosen[0].review_required and chosen[0].confidence < 0.55
    assert {c["text"] for c in conflicts[0]["candidates"]} == {"MRP Rs 180", "MRP Rs 160"}
    assert all(c["confidence"] == 0.98 for c in chosen[0].alternatives)


def test_agreement_does_not_inflate_confidence():
    chosen, conflicts = arbitrate([line("MRP Rs 180", "original", .79), line("MRP: Rs 180", "clahe", .8)])
    assert chosen[0].confidence == .8 and not conflicts
    assert len(chosen[0].variants) == 2


def test_distinct_mrps_are_not_deduplicated():
    chosen, conflicts = arbitrate([line("MRP Rs 180", "original"),
                                   line("MRP Rs 160", "original", bbox=(100, 150, 400, 185))])
    assert len(chosen) == 2 and not conflicts


def test_crop_segmentation_keeps_full_declaration_context():
    chosen, conflicts = arbitrate([
        line("MRP Rs 180.00", "original", .96),
        line("180.00", "inkjet", .999, bbox=(240, 100, 400, 135)),
        line("MRP", "inkjet", .99, bbox=(100, 100, 165, 135)),
    ])
    assert len(chosen) == 1 and chosen[0].text == "MRP Rs 180.00" and not conflicts


@pytest.mark.parametrize("degrees", [90, 270, -13, 17])
def test_rotated_variant_maps_back_to_original(degrees):
    base = fit_variant(np.full((320, 500, 3), 255, dtype=np.uint8))
    variant = rotate_variant(base, degrees)
    original = np.float32([[70, 100], [270, 100], [270, 130], [70, 130]])
    transformed = cv2.perspectiveTransform(original.reshape(-1, 1, 2), np.linalg.inv(variant.to_original))
    restored = map_polygon(transformed.reshape(-1, 2), variant.to_original, 500, 320)
    np.testing.assert_allclose(restored, original, atol=.001)


def test_upscale_box_mapping_and_height_are_original_pixels():
    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    variant = fit_variant(image, upscale=3)
    raw = ([[[[30, 60], [300, 60], [300, 120], [30, 120]], "MRP 180", .9]], 0)
    result = RapidOcrEngine._decode(raw, variant=variant, width=400, height=200)[0]
    assert result.bbox == (10, 20, 100, 40)
    assert result.height_px == pytest.approx(20 * .62)


def test_blank_image_does_not_claim_glare_or_occlusion():
    quality = assess_quality(np.full((800, 1000, 3), 255, dtype=np.uint8))
    codes = {i["code"] for i in quality["issues"]}
    assert {"blur_or_low_detail", "low_contrast", "overexposed_or_blank"} <= codes
    assert "possible_glare" not in codes
    assert quality["requires_rescan"]
    assert any("occlusion" in x for x in quality["unassessed"])


def test_tight_user_crop_warns_about_context_without_claiming_cropped_text():
    image = np.full((800, 1000, 3), 220, dtype=np.uint8)
    for y in range(80, 760, 80):
        cv2.putText(image, "MRP Rs 180 NET WT 200 g", (60, y), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (20, 20, 20), 2)
    quality = assess_quality(image, crop=[0.4, 0.4, 0.6, 0.6])
    issues = {item["code"]: item for item in quality["issues"]}
    assert quality["metrics"]["selected_original_fraction"] == pytest.approx(0.04)
    assert "tight_crop_context" in issues and "full-panel" in issues["tight_crop_context"]["action"]
    assert "possible_cropped_text" not in issues


def test_sparse_black_text_is_not_reported_as_low_contrast():
    image = np.full((800, 1000, 3), 245, dtype=np.uint8)
    for y in range(60, 700, 100):
        cv2.putText(image, "MRP 180 NET WT 200 g", (70, y), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 0, 0), 2)
    quality = assess_quality(image)
    assert not any(i["code"] == "low_contrast" for i in quality["issues"])


def test_bright_white_label_with_clear_print_is_not_reported_as_overexposed():
    image = np.full((800, 1000, 3), 248, dtype=np.uint8)
    for y in range(60, 760, 80):
        cv2.putText(image, "MRP 180 NET WT 200 g", (50, y), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (15, 15, 15), 2)
    quality = assess_quality(image)
    assert quality["metrics"]["saturated_fraction"] > .65
    assert not any(i["code"] == "overexposed_or_blank" for i in quality["issues"])


def test_second_model_tsv_preserves_language_and_word_confidence():
    from tula.ocr.auxiliary import decode_tsv
    variant = fit_variant(np.full((200, 400, 3), 255, dtype=np.uint8), upscale=2)
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    tsv += "5\t1\t1\t1\t1\t1\t20\t40\t100\t30\t92\tशुद्ध\n"
    tsv += "5\t1\t1\t1\t1\t2\t130\t40\t80\t30\t86\t200\n"
    lines = decode_tsv(tsv, variant, 400, 200)
    assert lines[0].text == "शुद्ध 200" and lines[0].confidence == .86
    assert lines[0].bbox == (10, 20, 105, 35)


def test_fragmented_print_requires_rescan():
    image = np.full((800, 1000, 3), 245, dtype=np.uint8)
    for y in range(200, 350, 5):
        for x in range(150, 800, 5):
            cv2.circle(image, (x, y), 1, (0, 0, 0), -1)
    quality = assess_quality(image)
    assert any(i["code"] == "fragmented_ink" for i in quality["issues"])
    assert quality["requires_rescan"]


def test_sensor_noise_does_not_disguise_heavy_blur():
    image = np.full((900, 1200, 3), 160, dtype=np.uint8)
    cv2.putText(image, "MRP 180", (100, 400), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 7)
    image = cv2.GaussianBlur(image, (0, 0), 14)
    noise = np.random.default_rng(26034).normal(0, 4, image.shape)
    image = np.clip(image.astype(float) + noise, 0, 255).astype(np.uint8)
    quality = assess_quality(image)
    assert quality["metrics"]["laplacian_variance"] > 35
    assert any(i["code"] == "blur_or_low_detail" for i in quality["issues"])
    assert quality["requires_rescan"]


def test_quality_locates_tiny_clipped_text():
    quality = assess_quality(np.full((300, 500, 3), 90, dtype=np.uint8), text_boxes=[(0, 10, 100, 19)])
    issues = {i["code"]: i for i in quality["issues"]}
    assert "low_resolution" in issues and "tiny_text" in issues and "possible_cropped_text" in issues
    assert issues["tiny_text"]["bbox"] == [0, 10, 100, 19]
    assert all(i["action"] for i in quality["issues"])


def test_inkjet_crop_is_bounded_and_does_not_modify_original():
    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    cv2.putText(image, "MRP 180", (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 1)
    before = image.copy()
    variants = list(targeted_variants(image, [line("MRP 180", "original", bbox=(40, 65, 250, 110))]))
    assert {v.name for v in variants} == {"inkjet_otsu_1", "inkjet_adaptive_1"}
    assert all(max(v.image.shape[:2]) <= 1600 for v in variants)
    assert all(v.to_original[0, 2] > 0 for v in variants)
    np.testing.assert_array_equal(image, before)


def test_engine_limits_passes_and_emits_diagnostics(tmp_path, monkeypatch):
    path = tmp_path / "capture.png"
    Image.new("RGB", (500, 300), "white").save(path)
    calls = []
    engine = RapidOcrEngine()

    def reader(image):
        calls.append(image.shape)
        return SimpleNamespace(boxes=[[[10, 10], [130, 10], [130, 30], [10, 30]]],
                               txts=["MRP Rs 180"], scores=[.98])

    engine._reader = reader
    monkeypatch.setenv("TULA_OCR_MAX_PASSES", "2")
    result = engine.read(str(path))
    assert len(calls) == 2 and len(result.passes) == 2
    assert result.width == 500 and result.height == 300
    assert result.quality["method"] and len(result.candidates) == 2
    assert all(0 <= p[0] <= 500 and 0 <= p[1] <= 300 for l in result.lines for p in l.polygon)


def test_failed_extra_pass_preserves_reading_and_discloses_failure(tmp_path, monkeypatch):
    path = tmp_path / "capture.png"
    Image.new("RGB", (500, 300), "white").save(path)
    engine = RapidOcrEngine()
    calls = 0

    def reader(_):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("model failed")
        return ([[[[10, 10], [130, 10], [130, 30], [10, 30]], "MRP 180", .98]], 0)

    engine._reader = reader
    monkeypatch.setenv("TULA_OCR_MAX_PASSES", "2")
    result = engine.read(str(path))
    assert result.lines and result.passes[-1]["status"] == "failed"
    assert any("pass" in w and "failed" in w for w in result.warnings)
