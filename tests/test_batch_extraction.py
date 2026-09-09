"""Unfamiliar identifiers, misleading cues and evidence-preserving batch extraction."""
import pytest

from tula.domain.enums import Panel
from tula.extract.batch import parse_batch
from tula.extract.intelligence import extract_intelligence
from tula.extract.pipeline import extract
from tula.ocr.base import OcrLine, OcrResult


def line(text, box=(10, 20, 260, 45), confidence=.96, frame="label.png", **kw):
    return OcrLine(text, box, confidence=confidence, frame=frame, **kw)


@pytest.mark.parametrize("raw,expected", [
    ("BATCHAB1234", "AB1234"), ("BATCHQX7_9-K", "QX7_9-K"), ("LOTRM482/7", "RM482/7"),
    ("LOT000875", "000875"), ("BatchZX-48", "ZX-48"), ("batchX7Y", "X7Y"),
    ("Batch: aq7-k", "aq7-k"), ("Lot no. 00124", "00124"), ("BATCH No9K", "9K"),
    ("Batch number: R7S.8", "R7S.8"), ("BATCH code: NOVA72", "NOVA72"),
    ("BATCH: LOT-002", "LOT-002"), ("बैच संख्या: ZR57", "ZR57"),
    ("Batch: AB7; MRP Rs 150", "AB7"), ("MFG 08/2026 BATCHQ9R1 EXP 09/2027", "Q9R1"),
    ("Batch A7.", "A7"), ("BATCH 7", "7"),
])
def test_joined_and_delimited_unfamiliar_codes_preserve_printed_value(raw, expected):
    assert parse_batch(raw) == expected
    fields = extract_intelligence([(Panel.BACK, line(raw))])["fields"]
    item = fields["batch_number"][0]
    assert item["value"] == expected and item["raw"] == raw
    assert item["sources"][0]["text"] == raw and item["sources"][0]["bbox"] == [10, 20, 260, 45]
    assert item["ocr_confidence"] == .96 and item["status"] == "detected"


@pytest.mark.parametrize("raw", [
    "BATCH", "Batch: SEE BOTTOM", "Batch printed below", "Batch FRESH", "BATCH PREMIUM QUALITY",
    "A lot of 25 portions", "SMALL BATCH25", "Handcrafted batch 2026", "Batch cooked daily",
    "LOTUS NATURAL TEA", "Lotus123 herbal tea", "BATCHING 25 PACKS", "RebatchAB42",
    "BATCH MRP 150", "BATCHMRP150", "Batch EXP09/2027", "BATCHEXP09/2027",
    "BATCH MFG 08/2026", "BATCHMFG082026", "Batch INR180", "Batch Rs.180",
    "Batch 08/2026", "BATCH08/2026", "Lot 2026-09-08", "BATCH09SEP2027",
    "Batch SEP2027", "BATCH 180.00", "BATCH10/-", "BATCH 150 INR", "BATCH180 g",
    "BATCH 200g", "Batch 20% more", "Batch AB1+9", "BATCHAB34EXP10/2027",
    "Batch: AB9\\12", "BATCHNO1234", "BATCHCODE1234",
])
def test_words_marketing_dates_prices_and_partial_codes_are_not_batch_values(raw):
    assert parse_batch(raw) is None
    assert not extract_intelligence([(Panel.BACK, line(raw))])["fields"].get("batch_number")


def test_joined_batch_does_not_swallow_neighboring_dates_or_price():
    source = line("MFG 08/2026 BATCHZ7R4 EXP 09/2027 MRP Rs 150")
    fields = extract_intelligence([(Panel.BACK, source)])["fields"]
    assert fields["batch_number"][0]["value"] == "Z7R4"
    assert fields["manufacturing_date"][0]["value"] == "2026-08"
    assert fields["expiry_date"][0]["value"] == "2027-09"
    assert fields["retail_sale_price"][0]["value"]["value"] == 150


def test_spatial_batch_value_preserves_weak_cue_and_its_conflicting_alternative():
    cue = line("Batch:", (0, 0, 75, 20), .51, review_required=True,
               alternatives=[{"text": "BATCHQ7R9", "confidence": .89, "variant": "reconnect"}])
    value = line("Q7R8", (85, 4, 145, 24), .95)
    fields = extract_intelligence([(Panel.BACK, cue), (Panel.BACK, value)])["fields"]
    item = fields["batch_number"][0]
    assert item["value"] == "Q7R8" and item["ocr_confidence"] == .51
    assert item["status"] == "needs_review" and item["method"] == "spatial_keyword_value"
    assert [span["bbox"] for span in item["sources"]] == [[0, 0, 75, 20], [85, 4, 145, 24]]
    assert item["ocr_alternatives"] == cue.alternatives
    assert item["candidates"][0]["value"] == "Q7R9"


def test_joined_batch_conflict_is_not_promoted_by_high_ocr_score():
    source = line("BATCHR9Z5", review_required=True,
                  alternatives=[{"text": "BATCHR9Z6", "confidence": .99, "variant": "threshold"}])
    item = extract_intelligence([(Panel.BACK, source)])["fields"]["batch_number"][0]
    assert item["method"] == "joined_batch_cue" and item["status"] == "needs_review"
    assert item["value"] == "R9Z5" and item["candidates"][0]["value"] == "R9Z6"


@pytest.mark.parametrize("value", ["MRP 150", "MFG 08/2026", "EXP 09/2027", "Net weight 200 g", "Q7R8"])
def test_batch_cue_does_not_take_other_fields_or_values_on_another_frame(value):
    source = line("Batch:", (0, 0, 75, 20))
    peer = line(value, (85, 4, 245, 24), frame="other.png" if value == "Q7R8" else "label.png")
    assert not extract_intelligence([(Panel.BACK, source), (Panel.BACK, peer)])["fields"].get("batch_number")


def test_different_readings_on_multiple_images_stay_reviewable():
    result = extract([(Panel.BACK, OcrResult(lines=[line("BATCHR7Q8", frame="one.png")])),
                      (Panel.BACK, OcrResult(lines=[line("BATCHR7Q9", frame="two.png")]))])
    fields = result.intelligence["fields"]["batch_number"]
    assert {item["value"] for item in fields} == {"R7Q8", "R7Q9"}
    assert all(item["status"] == "needs_review" for item in fields)
    assert {item["sources"][0]["frame"] for item in fields} == {"one.png", "two.png"}


def test_identical_repeated_text_retains_the_weak_source_and_conflict():
    weak = line("BATCHR7Q8", frame="two.png", confidence=.40, review_required=True,
                alternatives=[{"text": "BATCHR7Q9", "confidence": .91, "variant": "inkjet"}])
    fields = extract_intelligence([(Panel.BACK, line("BATCHR7Q8", frame="one.png")), (Panel.BACK, weak)])["fields"]["batch_number"]
    assert len(fields) == 1
    assert fields[0]["ocr_confidence"] == .40 and fields[0]["status"] == "needs_review"
    assert len(fields[0]["sources"]) == 2 and fields[0]["candidates"][0]["value"] == "R7Q9"


# ---------------------------------------------------------------------------
# The single-letter cue Indian packs actually print
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("B: 103A", "103A"), ("B:103A", "103A"), ("B # 7X2", "7X2"),
    ("B.No: AB1234", "AB1234"), ("B No: AB1234", "AB1234"),
    ("B: 103A EXP 09/2027", "103A"),
])
def test_single_letter_cue_recovers_a_stamped_code(raw, expected):
    # A real evaluation photograph stamps its batch as "B: 103A"; before this
    # the code survived only as an OCR alternative and never reached extraction.
    assert parse_batch(raw) == expected


@pytest.mark.parametrize("raw", [
    # One letter is weak evidence, so a bare number after it is not a batch.
    "B: 12", "B: 103", "B: 2026",
    # ...nor is it a cue at all when a preceding word owns the letter.
    "Vitamin B: 12A", "Vit B: 12A", "Vit. B: 12A", "Grade B: 12A", "Type B: 4X1",
    "Class B: 9Z", "विटामिन B: 12A",
    # ...nor when the letter is the tail of another word, or has no delimiter.
    "SUB: 12A", "B 103A", "B - 7X2",
    # ...and a date after the short cue stays a date.
    "B: 09/2027", "EXP B: 09/2027",
])
def test_single_letter_cue_refuses_everything_it_should(raw):
    assert parse_batch(raw) is None


def test_spelt_out_cue_keeps_its_older_looser_rule():
    # "Batch 7" was always accepted and must stay accepted: the extra evidence
    # demanded of the short form is not retro-applied to the spelt-out word.
    assert parse_batch("BATCH 7") == "7"
    assert parse_batch("Lot no. 00124") == "00124"


def test_officer_correction_contract_does_not_gain_the_short_cue():
    # STANDARD_CUE is what an officer's typed correction is validated against.
    # Machine inference may guess from one letter; a recorded correction may not.
    from tula.extract.batch import STANDARD_CUE

    assert STANDARD_CUE.match("Batch: 103A")
    assert not STANDARD_CUE.match("B: 103A")
