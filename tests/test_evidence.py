"""Evidence-model tests: what a capture is entitled to prove.

These protect the distinction the rest of the system is built on and which it
originally got wrong: "this package does not declare an MRP" and "I cannot see
an MRP in this photograph" are different statements, and only the first is a
violation. Every test here is a way of getting that wrong.

The failure being guarded against is not hypothetical. Ten real package
photographs off the web produced fifty-plus mandatory-declaration violations
against nationally-sold compliant products, because a presence check had no way
to say "I could not tell".
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tula.domain.enums import (
    AssuranceTier,
    CaptureCompleteness,
    Lane,
    Panel,
    Verdict,
)
from tula.domain.enums import (
    DeclarationClass as DC,
)
from tula.domain.models import Declaration, EvidenceCoverage, PackageFacts, Scan
from tula.extract import pipeline
from tula.forensics import gtin as gtin_forensics
from tula.ocr.base import OcrLine
from tula.rules.engine import RulesEngine
from tula.rules.evaluator import EvalContext, evaluate

# ---------------------------------------------------------------------------
# the coverage model itself
# ---------------------------------------------------------------------------


def _coverage(**kw) -> EvidenceCoverage:
    base = {
        "panels_captured": [Panel.PDP],
        "completeness": CaptureCompleteness.SINGLE,
        "frames": 1,
        "lines_read": 20,
        "legible_lines": 18,
        "mean_confidence": 0.9,
    }
    base.update(kw)
    return EvidenceCoverage(**base)


def test_nothing_read_can_prove_nothing():
    ok, why = _coverage(lines_read=0, legible_lines=0).can_prove_absence()
    assert ok is False
    assert "No text could be recognised" in why


def test_barely_legible_capture_cannot_prove_absence():
    ok, why = _coverage(lines_read=4, legible_lines=1).can_prove_absence()
    assert ok is False
    assert "too little" in why


def test_single_face_cannot_prove_a_declaration_absent():
    ok, why = _coverage(completeness=CaptureCompleteness.SINGLE).can_prove_absence()
    assert ok is False
    assert "any face of the package" in why


def test_complete_and_legible_capture_can_prove_absence():
    ok, why = _coverage(completeness=CaptureCompleteness.COMPLETE).can_prove_absence()
    assert ok is True
    assert why == ""


def test_legibility_is_measured_not_attested():
    """Ticking "whole package captured" must not talk up an unreadable frame.

    Coverage is a fact about the object that only the operator can supply.
    Legibility is a fact about the pixels, and the recogniser is the authority.
    """
    coverage = _coverage(
        completeness=CaptureCompleteness.COMPLETE,
        attested_complete=True,
        lines_read=0,
        legible_lines=0,
    )
    ok, _ = coverage.can_prove_absence()
    assert ok is False


def test_panel_specific_rule_needs_only_that_panel():
    """A declaration required on the PDP is provable absent from the PDP alone."""
    coverage = _coverage(completeness=CaptureCompleteness.SINGLE)
    assert coverage.can_prove_absence([Panel.PDP])[0] is True
    ok, why = coverage.can_prove_absence([Panel.BACK])
    assert ok is False
    assert "back" in why


# ---------------------------------------------------------------------------
# the `present` operator
# ---------------------------------------------------------------------------


def _decl_ctx(coverage, **decls):
    return EvalContext(facts={"decl": decls}, coverage=coverage)


def test_present_is_true_when_found_regardless_of_coverage():
    ctx = _decl_ctx(
        _coverage(lines_read=0, legible_lines=0),
        retail_sale_price=Declaration(klass=DC.RETAIL_SALE_PRICE, raw="MRP 45"),
    )
    assert evaluate(ctx, {"present": "$decl.retail_sale_price"}) is True


def test_present_is_unknown_when_absence_is_not_provable():
    ctx = _decl_ctx(_coverage(completeness=CaptureCompleteness.SINGLE))
    assert evaluate(ctx, {"present": "$decl.retail_sale_price"}) is None
    assert "any face of the package" in ctx.trace["absence_unprovable"]


def test_present_is_false_when_absence_is_provable():
    ctx = _decl_ctx(_coverage(completeness=CaptureCompleteness.COMPLETE))
    assert evaluate(ctx, {"present": "$decl.retail_sale_price"}) is False


def test_present_without_a_coverage_model_still_decides():
    """Hand-assembled facts mean exactly what they say."""
    assert evaluate(EvalContext(facts={"decl": {}}), {"present": "$decl.mrp"}) is False


def test_located_declaration_with_an_underivable_attribute_is_unknown():
    """Found the line, could not parse a value: a reading gap, not a label gap."""
    ctx = _decl_ctx(
        _coverage(completeness=CaptureCompleteness.COMPLETE),
        net_quantity=Declaration(klass=DC.NET_QUANTITY, raw="Net Wt.", norm={}),
    )
    assert evaluate(ctx, {"present": "$decl.net_quantity.norm.value"}) is None
    assert "could not be derived" in ctx.trace["parse_gap"]


def test_absent_is_the_negation_and_stays_three_valued():
    ctx = _decl_ctx(_coverage(completeness=CaptureCompleteness.SINGLE))
    assert evaluate(ctx, {"absent": "$decl.retail_sale_price"}) is None


# ---------------------------------------------------------------------------
# end to end through the engine
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    return RulesEngine.from_directory()


def _scan(coverage, lane=Lane.FIELD, tier=AssuranceTier.B):
    return Scan(
        scan_id="EV",
        lane=lane,
        tier=tier,
        packing_date=date(2026, 3, 1),
        captured_at=datetime(2026, 4, 1, tzinfo=UTC),
        coverage=coverage,
    )


def _verdict(findings, rule_id):
    return next((f.verdict for f in findings if f.rule_id == rule_id), None)


MRP_PRESENT = "LMPCR.R6.1.E.MRP_PRESENT"


def test_partial_capture_yields_no_violations_at_all(engine):
    """The regression this whole model exists for."""
    findings = engine.evaluate_all(
        _scan(_coverage(completeness=CaptureCompleteness.SINGLE)),
        PackageFacts(),
        {},
        {},
    )
    assert [f for f in findings if f.verdict is Verdict.VIOLATION] == []
    assert _verdict(findings, MRP_PRESENT) is Verdict.INCONCLUSIVE


def test_complete_capture_still_convicts(engine):
    """The control: the fix must not have quietly disabled the rules."""
    findings = engine.evaluate_all(
        _scan(_coverage(completeness=CaptureCompleteness.COMPLETE)),
        PackageFacts(),
        {},
        {},
    )
    assert _verdict(findings, MRP_PRESENT) is Verdict.VIOLATION


def test_unreadable_capture_yields_no_violations(engine):
    findings = engine.evaluate_all(
        _scan(
            _coverage(
                completeness=CaptureCompleteness.COMPLETE,
                lines_read=0,
                legible_lines=0,
            )
        ),
        PackageFacts(),
        {},
        {},
    )
    assert [f for f in findings if f.verdict is Verdict.VIOLATION] == []


@pytest.mark.parametrize(
    "lane,expected",
    [
        (Lane.FIELD, Verdict.VIOLATION),
        (Lane.PREMARKET, Verdict.VIOLATION),
        (Lane.CITIZEN, Verdict.ADVISORY),
        (Lane.MARKETPLACE, Verdict.ADVISORY),
    ],
)
def test_lane_decides_whether_a_finding_may_convict(engine, lane, expected):
    findings = engine.evaluate_all(
        _scan(_coverage(completeness=CaptureCompleteness.COMPLETE), lane=lane),
        PackageFacts(),
        {},
        {},
    )
    assert _verdict(findings, MRP_PRESENT) is expected


def test_advisory_still_reports_the_non_conformity(engine):
    """An advisory is the same finding, not a softer message."""
    findings = engine.evaluate_all(
        _scan(_coverage(completeness=CaptureCompleteness.COMPLETE), lane=Lane.CITIZEN),
        PackageFacts(),
        {},
        {},
    )
    finding = next(f for f in findings if f.rule_id == MRP_PRESENT)
    assert "no retail sale price" in finding.message.lower()
    assert "citizen submission" in finding.detail.lower()


def test_rule_may_declare_the_panel_that_settles_absence(engine):
    """The `absence_provable_from` knob, exercised on a real rule.

    Left empty across the shipped pack, deliberately: asserting which panel a
    declaration must appear on is a statutory question, and this pack is still
    an unverified draft. The mechanism is here and tested so a legal officer can
    turn it on per rule during transcription review.
    """
    rule = engine.pack.by_id(MRP_PRESENT)
    tightened = rule.model_copy(update={"absence_provable_from": [Panel.PDP]})
    patched = RulesEngine(
        engine.pack.model_copy(
            update={
                "rules": [tightened if r.id == MRP_PRESENT else r
                          for r in engine.pack.rules]
            }
        )
    )
    single_pdp = _coverage(
        completeness=CaptureCompleteness.SINGLE, panels_captured=[Panel.PDP]
    )
    findings = patched.evaluate_all(_scan(single_pdp), PackageFacts(), {}, {})
    assert _verdict(findings, MRP_PRESENT) is Verdict.VIOLATION


# ---------------------------------------------------------------------------
# barcode truncation
# ---------------------------------------------------------------------------


def test_clipped_leading_digit_is_recognised_as_truncation():
    """8901234567890 photographed with its first digit out of frame."""
    result = gtin_forensics.check("901234567890")
    assert result.length_valid is True  # 12 digits is a legal GTIN-12 length
    assert result.check_digit_valid is False
    assert result.truncation_of == "8901234567890"
    assert "clipped" in result.summary
    assert "fabricated" not in result.summary


def test_a_genuinely_wrong_number_is_not_excused():
    result = gtin_forensics.check("8901234567891")  # last digit wrong
    assert result.check_digit_valid is False
    assert result.truncation_of is None
    assert "misread" in result.summary


def test_valid_gtin_is_never_reported_as_truncated():
    assert gtin_forensics.check("8901234567890").truncation_of is None


def test_best_candidate_prefers_a_recoverable_read_over_noise():
    best = gtin_forensics.best_candidate(["12345678", "901234567890"])
    assert best.gtin == "901234567890"
    assert best.looks_truncated


# ---------------------------------------------------------------------------
# marketing copy is not a declaration
# ---------------------------------------------------------------------------


def _lines(*texts):
    return [
        (Panel.PDP, OcrLine(text=t, bbox=(0, i * 20, 200, i * 20 + 16), confidence=0.95))
        for i, t in enumerate(texts)
    ]


def test_commodity_word_inside_marketing_copy_is_not_a_generic_name():
    assert pipeline._find_generic(_lines("MADE WITH QUALITY SPICES")) is None


def test_standalone_commodity_word_is_a_generic_name():
    found = pipeline._find_generic(_lines("Masala Noodles"))
    assert found is not None and found[0] == "noodles"


def test_longest_lexicon_term_wins():
    found = pipeline._find_generic(_lines("Mustard Oil"))
    assert found[0] == "mustard oil"


def test_prose_is_not_a_generic_name_however_long():
    assert pipeline._find_generic(_lines("contains iron and the goodness of milk")) is None


def test_brand_guess_rejects_a_price_flash():
    """"12.5% EXTRA" set large is shelf-shout, not the brand."""
    lines = _lines("12.5% EXTRA", "Parle-G")
    brand = pipeline._guess_brand(lines, None)
    assert brand is not None and "Parle" in brand[1].text


def test_marketing_phrase_broken_across_ocr_lines_is_still_rejected():
    """Where the recogniser breaks a line is an artefact of layout, not language.

    Taken from a real RapidOCR read of a Maggi packet, which returns "MADeWiTH"
    and "QUALiTY SPiCeS" as two lines -- spaces dropped and case mangled. Judged
    line by line, the second half reads as a declaration that the commodity is
    "spices", and Rule 6(1)(b) passed on a pack that never says what it is.
    """
    assert pipeline._find_generic(_lines("MADeWiTH", "QUALiTY SPiCeS")) is None


def test_a_real_generic_name_after_an_unrelated_line_survives():
    """The window must not swallow legitimate declarations."""
    found = pipeline._find_generic(_lines("Everest", "Garam Masala"))
    assert found is not None and found[0] == "garam masala"


def test_lexicon_term_must_match_as_a_word_not_a_substring():
    """"masala" inside "emasala" is a misread, not a declaration.

    RapidOCR returns "emasala taste" for "...favourite masala taste", welding
    the tail of the previous word on. A substring search reported the pack as
    declaring a commodity called "masala" on the strength of that.
    """
    assert pipeline._find_generic(_lines("Your FaVoURiTe", "emasala taste")) is None
    found = pipeline._find_generic(_lines("Garam Masala"))
    assert found is not None and found[0] == "garam masala"
