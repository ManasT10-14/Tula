"""Scoring integrity tests; actual image accuracy comes from the separate run."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("real_label_evaluation", ROOT / "scripts/evaluate_real_labels.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def annotation():
    return {"field": "retail_sale_price", "status": "readable", "value_path": "value",
            "expected_values": [20], "regions": [{"text": "20", "bbox": [10, 10, 110, 50]}]}


def record():
    return {"id": "sample", "fields": [annotation()],
            "unknown_default": {"status": "not_visible", "reason": "Other sides unseen"}}


def extracted(value=20, *, bbox=(10, 10, 110, 50), confidence=.9, review=False):
    return {"declarations": {"retail_sale_price": {
        "norm": {"value": value, "requires_review": review}, "bbox": bbox,
        "confidence": confidence, "raw": f"MRP Rs {value}"}}, "intelligence": {}}


@pytest.mark.parametrize(("actual", "printed", "matches"), [
    ("MRP Rs 20", "20", True), ("₹20/-", "20", True),
    ("MRP Rs 200", "20", False), ("20.50", "20", False),
    ("20/2026", "20", False), ("01/20/2026", "20", False),
    ("1800", "180.00", False), ("AB12345", "AB1234", False),
    ("MFG 08/2026", "08/2026", True), ("MFG 082026", "08/2026", False),
    ("PARLE-G", "parle-g", True), ("PARLEG", "Parle-G", False),
    ("Good  Day", "Good Day", True), ("GOODDAY", "Good Day", True),
])
def test_ocr_recovery_preserves_numeric_and_token_boundaries(actual, printed, matches):
    assert evaluation.text_contains(actual, printed) is matches


def test_structured_exact_is_not_global_substring_or_fuzzy_price_match():
    assert evaluation.exact_value(20.0, 20)
    assert not evaluation.exact_value(20.01, 20)
    assert not evaluation.exact_value(None, 20)
    assert not evaluation.exact_value(True, 1)
    assert not evaluation.exact_value("1800", 180)
    assert not evaluation.exact_value("01/02/2026", "02/01/2026")


def test_right_price_from_wrong_region_is_not_field_exact_or_ocr_recovery():
    wrong_location = [300, 300, 400, 340]
    rows = evaluation.score_image(record(), extracted(bbox=wrong_location),
                                  {"lines": [{"text": "Rs 20", "bbox": wrong_location}]},
                                  ["retail_sale_price"])
    assert rows[0]["outcome"] == "incorrect_accepted"
    assert not rows[0]["selected_exact"]
    assert not rows[0]["ocr_candidate_recovery"]


def test_accepted_brand_alternative_must_match_its_own_printed_region():
    target = {"value_path": "name", "expected_values": ["Britannia", "Bourbon"],
              "regions": [{"text": "Britannia", "bbox": [0, 0, 100, 30]},
                          {"text": "Bourbon", "bbox": [0, 60, 180, 100]}]}
    item = {"value": {"name": "Britannia"}, "boxes": [[0, 60, 180, 100]]}
    assert not evaluation.prediction_matches(item, target)
    item["boxes"] = [[0, 0, 100, 30]]
    assert evaluation.prediction_matches(item, target)


def test_correct_ocr_text_in_another_field_does_not_count_as_correct_extraction():
    data = extracted()
    data["declarations"]["brand"] = data["declarations"].pop("retail_sale_price")
    rows = evaluation.score_image(record(), data,
                                  {"lines": [{"text": "Rs 20", "bbox": [10, 10, 110, 50]}]},
                                  ["retail_sale_price"])
    assert rows[0]["abstained"]
    assert rows[0]["ocr_selected_recovery"]
    assert not rows[0]["selected_exact"]
    assert rows[0]["failure_stage"] == "extraction_classification_or_normalization"


def test_review_held_correct_candidate_is_not_silent_accepted_success():
    rows = evaluation.score_image(record(), extracted(confidence=.54, review=True), {}, ["retail_sale_price"])
    assert rows[0]["selected_exact"]
    assert rows[0]["abstained"]
    assert not rows[0]["accepted_exact"]
    assert rows[0]["outcome"] == "exact_held_for_review"


def test_explicit_provenance_status_and_minimum_score_control_acceptance():
    data = extracted()
    data["declarations"]["retail_sale_price"]["provenance"] = {
        "status": "needs_review", "ocr_confidence": .54,
        "sources": [{"bbox": [10, 10, 110, 50]}]}
    row = evaluation.score_image(record(), data, {}, ["retail_sale_price"])[0]
    assert row["selected_exact"] and row["abstained"]
    assert not row["accepted_exact"]
    data["declarations"]["retail_sale_price"]["norm"] = {}
    data["declarations"]["retail_sale_price"]["provenance"]["status"] = "cue_only"
    row = evaluation.score_image(record(), data, {}, ["retail_sale_price"])[0]
    assert row["abstained"] and not row["incorrect_accepted"]


def test_missing_chosen_value_can_still_retain_correct_structured_alternative():
    data = extracted(value=None, review=True)
    data["declarations"]["retail_sale_price"]["norm"]["candidates"] = [{"value": 200}, {"value": 20}]
    row = evaluation.score_image(record(), data, {}, ["retail_sale_price"])[0]
    assert not row["selected_exact"]
    assert row["structured_candidate_recovery"]
    assert row["abstained"]


def test_conflicting_ocr_alternative_recovery_is_separate_from_selected():
    row = evaluation.score_image(record(), extracted(value=200),
                                 {"lines": [{"text": "Rs 200", "bbox": [10, 10, 110, 50]}],
                                  "candidates": [{"text": "Rs 20", "bbox": [10, 10, 110, 50]}]},
                                 ["retail_sale_price"])[0]
    assert not row["ocr_selected_recovery"]
    assert row["ocr_candidate_recovery"]
    assert row["incorrect_accepted"]
    assert row["failure_stage"] == "candidate_selection_or_review_filtering"


def test_separate_processing_passes_cannot_be_welded_into_recovered_text():
    target = {"regions": [{"text": "Good Day", "bbox": [0, 0, 200, 100]}]}
    items = [{"text": "Good", "bbox": [10, 10, 70, 40], "variant": "original"},
             {"text": "Day", "bbox": [75, 10, 140, 40], "variant": "threshold"}]
    assert not evaluation.text_recovered(items, target)
    items[1]["variant"] = "original"
    assert evaluation.text_recovered(items, target)


def test_unknowns_are_reported_but_never_in_accuracy_denominator():
    data = extracted()
    data["declarations"]["net_quantity"] = {
        "norm": {"value": 70, "unit": "g"}, "confidence": .95, "raw": "a serve is 70 g"}
    rows = evaluation.score_image(record(), data, {}, ["retail_sale_price", "net_quantity", "expiry_date"])
    result = evaluation.aggregate([{"id": "sample", "fields": rows}, {"id": "crashed", "error": "engine failed"}],
                                  ["retail_sale_price", "net_quantity", "expiry_date"])
    assert result["totals"]["readable"] == 1
    assert result["totals"]["unknown"] == 2
    assert result["totals"]["unknown_with_output"] == 1
    assert result["by_field"]["expiry_date"]["accepted_exact"] == 0
    assert result["completed_images"] == 1
    assert len(result["runtime_errors"]) == 1


def mini_dataset(tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGB", (120, 80), "white").save(image)
    data = {"schema_version": 1, "target_fields": ["retail_sale_price"],
            "images": [{**record(), "path": "image.png", "sha256": evaluation.sha256(image), "size": [120, 80]}]}
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def test_annotation_image_integrity_detects_changed_pixels(tmp_path):
    path, _ = mini_dataset(tmp_path)
    evaluation.load_annotations(path, tmp_path)
    Image.new("RGB", (120, 80), "black").save(tmp_path / "image.png")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        evaluation.load_annotations(path, tmp_path)


def test_duplicate_hash_cannot_double_count_under_a_new_id(tmp_path):
    path, data = mini_dataset(tmp_path)
    second = copy.deepcopy(data["images"][0])
    second["id"] = "different-name-same-content"
    data["images"].append(second)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate image hash"):
        evaluation.load_annotations(path, tmp_path)


def test_bad_box_or_package_wide_absence_claim_is_rejected(tmp_path):
    path, data = mini_dataset(tmp_path)
    data["images"][0]["fields"][0]["regions"][0]["bbox"] = [1, 2, 999, 88]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid annotation"):
        evaluation.load_annotations(path, tmp_path)
    path, data = mini_dataset(tmp_path)
    data["images"][0]["unknown_default"]["status"] = "absent_from_package"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="package-wide absence"):
        evaluation.load_annotations(path, tmp_path)


def test_retained_dataset_covers_ten_unique_photos_and_honest_missing_targets():
    data = evaluation.load_annotations(evaluation.ANNOTATIONS)
    assert len(data["images"]) == len({r["sha256"] for r in data["images"]}) == 10
    readable = [f for image in data["images"] for f in image["fields"] if f["status"] == "readable"]
    assert len(readable) == 16
    assert {field["field"] for field in readable} == {"brand", "generic_name", "retail_sale_price"}
    assert "unblinded" in data["protocol"]
    assert "No package-completeness" in data["scope"]
