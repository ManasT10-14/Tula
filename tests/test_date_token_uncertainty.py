"""Literal OCR ambiguity stays local and never supplies corrected date truth."""
from __future__ import annotations

import copy
import hashlib

import pytest
from PIL import Image

from tula.ocr.arbitrate import arbitrate, date_token_uncertainty
from tula.ocr.base import OcrLine
from tula.ocr.engines import RapidOcrEngine


def line(text, score=.97, variant="original", box=(10, 10, 300, 45)):
    return OcrLine(text=text, bbox=box, confidence=score, variants=[variant])


@pytest.mark.parametrize("raw", [
    "0CT/26", "N0V-2028", "5EP.27", "FE8RUARY/2029", "APR1L / 30",
    "MFG: 14/0ct/2028", "USE BY N0V / 2027", "Date: 0CT/26 - N0V/26",
])
def test_month_like_uncertainty_keeps_only_the_actual_reading(raw):
    original = line(raw)
    before = copy.deepcopy(original)
    chosen, diagnostics = arbitrate([original])
    assert original == before
    assert chosen[0].text == raw and chosen[0].review_required
    assert chosen[0].confidence == .54
    assert chosen[0].alternatives[0]["text"] == raw
    assert chosen[0].alternatives[0]["confidence"] == .97
    assert diagnostics[0]["kind"] == "date_token_uncertainty"
    assert diagnostics[0]["bbox"] == list(original.bbox)
    assert all(token["raw"] in raw for token in diagnostics[0]["date_token_diagnostics"])
    assert "corrected" not in diagnostics[0]


@pytest.mark.parametrize("raw", [
    "OCT/26", "September/2027", "EXP 03/04/2028", "2028-03-04", "Best before 24 months",
    "13 JN 02", "MAI/28", "XYZ/2030", "X1Y/2028", "A0B/26", "J4N/26",
    "OCTOBER SALE", "SAVE 50%", "BUY 2 GET 1", "5 FOR 26", "MRP Rs. 20.00",
    "SKU: 0CT/26", "MODEL: N0V/2027", "Batch No: 5EP/28", "LOT 0CT/26",
    "PROMO CODE: N0V/27", "OFFER CODE 0CT/26", "PART: 0CT/26", "Reference 0CT/26",
    "serial 0CT/26", "EXP 01/26 SKU 0CT/26", "SKU: OCT/26 - N0V/26",
])
def test_valid_dates_unfamiliar_codes_and_explicit_nondate_contexts_are_not_rewritten(raw):
    assert not date_token_uncertainty(raw)
    chosen, diagnostics = arbitrate([line(raw)])
    assert chosen[0].text == raw and chosen[0].confidence == .97
    assert not chosen[0].review_required and not diagnostics


def test_later_printed_date_cue_ends_the_explicit_code_context():
    tokens = date_token_uncertainty("SKU: 0CT/26 EXP: N0V/2028")
    assert [token["raw"] for token in tokens] == ["N0V/2028"]


def test_independent_agreement_is_not_proof_of_literal_correctness():
    raw = "JUL/25 - 0CT/26"
    chosen, diagnostics = arbitrate([line(raw), line(raw, .98, "clahe_upscale"),
                                     line(raw, .81, "tesseract")])
    assert len(chosen) == 1 and chosen[0].review_required
    assert chosen[0].text == raw
    assert [alternative["text"] for alternative in chosen[0].alternatives] == [raw] * 3
    assert diagnostics[0]["kind"] == "date_token_uncertainty"


def test_actual_same_extent_month_digit_disagreement_is_not_hidden_by_numeric_subset():
    chosen, diagnostics = arbitrate([line("EXP OCT/26", .99), line("EXP 0CT/26", .95, "tesseract")])
    assert len(chosen) == 1 and chosen[0].review_required
    assert diagnostics[0]["kind"] == "recognition_disagreement"
    assert {item["text"] for item in diagnostics[0]["candidates"]} == {"EXP OCT/26", "EXP 0CT/26"}


def test_same_extent_date_separator_disagreement_remains_literal():
    chosen, diagnostics = arbitrate([line("EXP OCT/26"), line("EXP OCT-26", .96, "tesseract")])
    assert chosen[0].review_required
    assert diagnostics[0]["kind"] == "recognition_disagreement"


def test_same_extent_whitespace_and_case_variants_are_not_a_conflict():
    chosen, diagnostics = arbitrate([line("EXP OCT / 26"), line("exp oct/26", .96, "tesseract")])
    assert len(chosen) == 1 and not chosen[0].review_required and not diagnostics


def test_weak_confused_candidate_does_not_override_a_clear_literal_month():
    chosen, diagnostics = arbitrate([line("EXP OCT/26", .99), line("EXP 0CT/26", .20, "tesseract")])
    assert not chosen[0].review_required and not diagnostics
    assert chosen[0].text == "EXP OCT/26"


def test_crop_of_one_of_two_real_dates_does_not_invent_disagreement():
    chosen, diagnostics = arbitrate([
        line("MFG OCT/26 EXP NOV/27", .98, box=(10, 10, 600, 45)),
        line("NOV/27", .96, "tesseract", box=(420, 10, 600, 45)),
    ])
    assert all(not item.review_required for item in chosen)
    assert not diagnostics


def test_uncertainty_is_local_and_separate_legitimate_code_is_preserved():
    originals = [line("N0V/2028", box=(10, 10, 200, 45)),
                 line("SKU: 0CT/26", box=(10, 100, 300, 145)),
                 line("Rs.600.00", box=(10, 200, 300, 245))]
    chosen, diagnostics = arbitrate(originals)
    assert [item.review_required for item in chosen] == [True, False, False]
    assert len(diagnostics) == 1 and diagnostics[0]["bbox"] == [10, 10, 200, 45]


def test_label_on_another_region_cannot_suppress_uncertain_date_token():
    chosen, diagnostics = arbitrate([line("SKU:", box=(10, 100, 100, 140)),
                                     line("0CT/26", box=(400, 10, 600, 45))])
    assert chosen[1].review_required and len(diagnostics) == 1


def test_uncertain_code_shape_has_no_assigned_date_role_or_normalized_value():
    _, diagnostics = arbitrate([line("0CT/26")])
    assert set(diagnostics[0]) == {"bbox", "selected_text", "candidates", "kind",
                                  "date_token_diagnostics", "reason", "action"}
    assert not any(key in diagnostics[0] for key in ("expiry", "manufacture", "value", "iso", "corrected_text"))


@pytest.mark.parametrize("readings,kind,warning", [
    (["N0V/28", "N0V/28"], "date_token_uncertainty", "uncertain date-like tokens"),
    (["EXP NOV/28", "EXP N0V/28"], "recognition_disagreement", "conflicting OCR candidates"),
])
def test_engine_discloses_kind_preserves_original_and_respects_pass_budget(tmp_path, monkeypatch,
                                                                         readings, kind, warning):
    # Explicit adapter stub tests the wiring; actual OCR is separately run on
    # retained originals. This test is not an image-recognition accuracy claim.
    path = tmp_path / "capture.png"
    Image.new("RGB", (500, 300), "white").save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    engine, calls = RapidOcrEngine(), []

    def reader(image):
        text = readings[len(calls)]
        calls.append(text)
        sx, sy = image.shape[1] / 500, image.shape[0] / 300
        polygon = [[x * sx, y * sy] for x, y in ((10, 10), (290, 10), (290, 45), (10, 45))]
        return ([[polygon, text, .97]], 0)

    engine._reader = reader
    monkeypatch.setenv("TULA_OCR_MAX_PASSES", "2")
    result = engine.read(str(path))
    assert len(calls) == len(result.passes) == 2
    assert len(result.conflicts) == 1 and result.conflicts[0]["kind"] == kind
    assert any(warning in value for value in result.warnings)
    if kind == "date_token_uncertainty":
        assert not any("conflicting OCR candidates" in value for value in result.warnings)
    assert {candidate["text"] for candidate in result.candidates} == set(readings)
    assert all(candidate["confidence"] == .97 for candidate in result.candidates)
    assert all(0 <= x <= 500 and 0 <= y <= 300 for line in result.lines for x, y in line.polygon)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
