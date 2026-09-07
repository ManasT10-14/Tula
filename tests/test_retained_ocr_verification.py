"""The focused evaluation distinguishes literal text, values, units and trust."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("verify_retained_ocr")


def output(field, value, *, status="detected", box=None, confidence=.9):
    return {"declarations": {}, "intelligence": {"fields": {field: [
        {"value": value, "raw": "source", "status": status, "ocr_confidence": confidence,
         "sources": [{"bbox": box or [50, 130, 345, 190]}], "candidates": []}
    ]}}}


def test_wrong_unit_is_not_a_correct_quantity(verifier):
    row = verifier.verify_fields({"lines": [], "candidates": []},
        output("net_quantity", {"value": 200., "unit": "mg"}), verifier.TARGETS[:1])[0]
    assert row["incorrect_accepted"] and not row["structured_exact"]


def test_literal_month_year_and_normalized_date_are_scored_separately(verifier):
    target = verifier.TARGETS[2]
    ocr = {"lines": [{"text": "MFG 08/2026", "bbox": target["bbox"]}], "candidates": []}
    row = verifier.verify_fields(ocr, output("manufacturing_date", "2026-08", box=target["bbox"]), [target])[0]
    assert row["selected_literal"] and row["accepted_exact"] and not row["incorrect_accepted"]
    ocr["lines"][0]["text"] = "MFG 082026"
    assert not verifier.verify_fields(ocr, output("manufacturing_date", "2026-08", box=target["bbox"]), [target])[0]["selected_literal"]


def test_review_candidate_does_not_count_as_accepted_and_requires_source_region(verifier):
    target = verifier.TARGETS[1]
    correct = output("retail_sale_price", {"value": 180., "currency": "INR"},
                     status="needs_review", box=target["bbox"], confidence=.19)
    row = verifier.verify_fields({"lines": [], "candidates": []}, correct, [target])[0]
    assert row["structured_exact"] and not row["accepted_exact"] and not row["incorrect_accepted"]
    wrong_region = output("retail_sale_price", {"value": 180., "currency": "INR"}, box=[700, 500, 900, 600])
    row = verifier.verify_fields({"lines": [], "candidates": []}, wrong_region, [target])[0]
    assert row["incorrect_accepted"] and not row["structured_exact"]
