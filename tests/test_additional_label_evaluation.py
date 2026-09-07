"""Scoring safeguards, independent of expensive real-photo OCR execution."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("additional_evaluation", ROOT / "scripts/evaluate_additional_labels.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def target(field="retail_sale_price"):
    return {"id": "price", "kind": "printed_price", "literal": "Rs.600.00", "bbox": [10, 10, 110, 40],
            "structured_scored": True, "structured_field": field,
            "expected": {"value": 600, "currency": "INR"}}


def item(value=None, **kwargs):
    return {"value": value if value is not None else {"value": 600, "currency": "INR"},
            "raw": "Rs.600.00", "status": "detected", "ocr_confidence": .95,
            "sources": [{"frame": "original.jpg", "bbox": [10, 10, 110, 40]}],
            "candidates": [], **kwargs}


def score(items, targets=None, ocr=None):
    return evaluation.score_image({"path": "original.jpg", "targets": targets or [target()]},
        {"intelligence": {"fields": {"retail_sale_price": items}}}, ocr or {})[0][0]


@pytest.mark.parametrize(("actual", "literal", "expected"), [
    ("Rs. 600.00", "Rs.600.00", True), ("RS.1.26 per g", "Rs.1.26 per g", True),
    ("Rs60000", "Rs.600.00", False), ("Rs.600.001", "Rs.600.00", False),
    ("B:103A", "103A", True), ("B:103AB", "103A", False), ("B:1103A", "103A", False),
    ("13 JN02", "13 JN 02", True), ("13 JN 02", "13 JN 20", False),
    ("E:12/04/2024", "12/04/2024", True), ("12-04-2024", "12/04/2024", False),
    ("JUL/25 - OCT/26", "OCT/26", True), ("JUL/25", "JUL/2025", False),
])
def test_literal_matching_keeps_punctuation_digits_and_token_boundaries(actual, literal, expected):
    assert evaluation.contains_literal(actual, literal) is expected


def test_literal_cannot_join_different_passes_or_frames_or_nonlocal_text():
    first = {"text": "Rs.", "bbox": [10, 10, 30, 40], "variant": "original", "frame": "original.jpg"}
    second = {"text": "600.00", "bbox": [31, 10, 100, 40], "variant": "original", "frame": "original.jpg"}
    assert evaluation.literal_evidence([first, second], target(), "original.jpg", combine=True)
    assert not evaluation.literal_evidence([first, second], target(), "original.jpg", combine=False)
    for change in ({"variant": "threshold"}, {"frame": "another.jpg"}, {"bbox": [500, 500, 600, 550]}):
        assert not evaluation.literal_evidence([first, {**second, **change}], target(), "original.jpg", combine=True)


def test_correct_price_needs_currency_and_original_source():
    assert evaluation.matches(item(), target(), "original.jpg")
    for altered in (item({"value": 600}), item({"value": 600, "currency": "USD"}),
                    item(sources=[]), item(sources=[{"bbox": [10, 10, 110, 40], "frame": "other.jpg"}]),
                    item(sources=[{"bbox": [500, 500, 600, 550], "frame": "original.jpg"}])):
        assert not evaluation.matches(altered, target(), "original.jpg")


def test_price_shape_hypothesis_can_match_but_must_abstain_from_acceptance():
    prediction = item({"value": 600, "currency": "INR", "currency_verified": False},
                      method="pixel_currency_context_candidate", currency_evidence={"symbol_similarity": .9})
    row = score([prediction])
    assert row["selected_exact"] and row["exact_held_for_review"] and row["abstained"]
    assert not row["accepted_exact"]
    assert row["predictions"][0]["currency_evidence"] == {"symbol_similarity": .9}


def test_conflicting_wrong_accepted_value_is_not_hidden_by_matching_candidate():
    row = score([item(), item({"value": 6000, "currency": "INR"},
                             candidates=[{"value": {"value": 600, "currency": "INR"}}])])
    assert row["wrong_accepted"] and row["accepted_exact"] and row["candidate_exact"]
    assert row["outcome"] == "wrong_accepted"


def test_candidates_do_not_count_as_selected_or_accepted():
    row = score([item({"value": 60, "currency": "INR"}, status="needs_review",
                     candidates=[{"value": {"value": 600, "currency": "INR"}}])])
    assert row["candidate_exact"] and row["abstained"]
    assert not row["selected_exact"] and not row["accepted_exact"]


@pytest.mark.parametrize("score_value", [.54, None, float("nan"), True])
def test_untrusted_scores_cannot_accept(score_value):
    assert not evaluation.accepted(item(ocr_confidence=score_value))


def test_unit_price_basis_and_currency_require_emitted_evidence():
    unit = {**target("unit_sale_price"), "expected": {"value": 1.26, "currency": "INR", "basis_value": 1, "basis_unit": "g"}}
    value = {"value": 1.26, "per_value": 1, "per_unit": "g", "raw": "Rs.1.26 per g"}
    assert evaluation.matches(item(value), unit, "original.jpg")
    assert evaluation.normalized_value(value, "unit_sale_price")["currency_basis"] == "recognized unit-price raw token"
    for altered in ({**value, "raw": ""}, {**value, "raw": "$1.26/g"}, {**value, "per_unit": "kg"},
                    {**value, "per_value": 100}, {**value, "currency": "USD"}):
        assert not evaluation.matches(item(altered), unit, "original.jpg")


def test_batch_scalar_is_exact_without_punctuation_or_digit_repairs():
    batch = {**target("batch_number"), "expected": {"value": "103A"}}
    assert evaluation.matches(item("103A"), batch, "original.jpg")
    assert not evaluation.matches(item("1O3A"), batch, "original.jpg")


def test_ambiguous_dates_are_literal_only_and_runtime_errors_keep_denominators():
    ambiguous = {**target(), "id": "date", "literal": "JUL/25", "structured_scored": False, "structured_field": None}
    data = {"images": [{"id": "one", "targets": [target(), ambiguous]}]}
    summary = evaluation.summarize(data, [{"id": "one", "error": "engine unavailable"}])
    assert summary["literal_targets"] == 2 and summary["structured_targets"] == 1
    assert summary["completed_photos"] == 0 and summary["runtime_errors"]
    assert "accepted_exact" not in score([], targets=[ambiguous])


def dataset(tmp_path):
    image = tmp_path / "photo.png"
    Image.new("RGB", (120, 50), "white").save(image)
    data = {"schema_version": 1, "images": [{"id": "one", "path": "photo.png", "size": [120, 50],
            "sha256": evaluation.sha256(image), "targets": [target()]}]}
    path = tmp_path / "annotations.v1.json"
    save_dataset(path, data)
    return path, data


def save_dataset(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    path.with_name("FROZEN.json").write_text(json.dumps({"annotation_sha256": evaluation.sha256(path),
        "literal_targets": 1, "structured_targets": 1}), encoding="utf-8")


def test_frozen_inputs_and_duplicate_detection(tmp_path):
    path, data = dataset(tmp_path)
    assert evaluation.load_annotations(path, tmp_path) == data
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="annotation SHA"):
        evaluation.load_annotations(path, tmp_path)
    save_dataset(path, data)
    changed = copy.deepcopy(data)
    changed["images"].append({**changed["images"][0], "id": "two"})
    save_dataset(path, changed)
    with pytest.raises(ValueError, match="Duplicate image"):
        evaluation.load_annotations(path, tmp_path)
    save_dataset(path, data)
    (tmp_path / "photo.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="photo SHA"):
        evaluation.load_annotations(path, tmp_path)


def test_box_bounds_and_path_escape_rejected(tmp_path):
    path, data = dataset(tmp_path)
    data["images"][0]["targets"][0]["bbox"][2] = 121
    save_dataset(path, data)
    with pytest.raises(ValueError, match="coordinate"):
        evaluation.load_annotations(path, tmp_path)
    data["images"][0]["path"] = "../escape.png"
    save_dataset(path, data)
    with pytest.raises(ValueError, match="escapes"):
        evaluation.load_annotations(path, tmp_path)


def test_retained_output_is_never_overwritten(tmp_path):
    path, _ = dataset(tmp_path)
    output = tmp_path / "retained"
    output.mkdir()
    sentinel = output / "results.json"
    sentinel.write_text("previous evidence")
    with pytest.raises(FileExistsError, match="immutable"):
        evaluation.run(path, output, tmp_path)
    assert sentinel.read_text() == "previous evidence"


def test_actual_frozen_manifest_has_four_photos_fourteen_literals_five_structured():
    data = evaluation.load_annotations(evaluation.ANNOTATIONS)
    assert len(data["images"]) == 4
    assert sum(len(image["targets"]) for image in data["images"]) == 14
    assert sum(target["structured_scored"] for image in data["images"] for target in image["targets"]) == 5
