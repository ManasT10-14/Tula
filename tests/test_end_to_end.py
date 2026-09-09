"""Golden test for the whole vertical slice.

The fixture label was built so each violation exercises a different
differentiator. If any of these stops firing, the demo has quietly broken --
which is exactly the failure this test exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.domain.enums import AssuranceTier, Panel, Verdict
from tula.domain.enums import DeclarationClass as DC
from tula.report import docx_export, pdf, render

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "biscuit-front.jpg"

EXPECTED_VIOLATIONS = {
    "LMPCR.R6.1.C.UNIT_SYMBOL",              # "200 gms" is not a prescribed symbol
    "LMPCR.R6.1.E.MRP_FORM",                 # no "inclusive of all taxes"
    "LMPCR.R6.1.F.UNIT_PRICE_ARITHMETIC",    # wrong declared price and statutory per-gram basis
}


@pytest.fixture(scope="module")
def analysis():
    return analyse(
        [Capture(str(SAMPLE), Panel.PDP)],
        AnalyseOptions(engine_name="fixture", operator="INSP-KA-0114", legal_context={
            "category": "food", "category_confirmed": True,
            "bundle_type": "single", "bundle_confirmed": True,
            "shape": "rectangular", "shape_confirmed": True,
            "imported_confirmed": True, "is_imported": False,
            "assessment_date": "2026-09-07", "assessment_date_confirmed": True,
            "context_basis": "Explicit historical test fixture facts"}),
    )


def test_overall_determination(analysis):
    assert analysis.overall is Verdict.VIOLATION
    assert analysis.engine == "fixture"
    assert analysis.rules_version == "2026.09.10-expanded-1"


def test_exactly_the_expected_violations_fire(analysis):
    found = {f.rule_id for f in analysis.violations}
    assert found == EXPECTED_VIOLATIONS


def test_evidence_tier_is_earned_not_assumed(analysis):
    # the fixture supplies a depth-derived scale, which is Tier B
    assert analysis.scan.tier is AssuranceTier.B
    assert analysis.scan.scales, "at least one scale source must be recorded"


def test_net_quantity_measurement_and_threshold(analysis):
    finding = next(f for f in analysis.findings if f.rule_id == "LMPCR.R8.2.NETQTY_HEIGHT")
    assert finding.measured is not None
    assert 1.3 < finding.measured.value < 1.55
    assert finding.measured.uncertainty > 0
    assert finding.threshold == 2.5
    # This legacy fixture has text and metadata but no photograph. Its box
    # proxy cannot substantiate a physical height violation.
    assert finding.verdict is Verdict.INCONCLUSIVE
    assert finding.measured.tier is AssuranceTier.C
    # the whole interval must sit below the limit for a violation to be recorded
    assert finding.measured.upper < finding.threshold
    assert "216" in (finding.threshold_basis or "")


def test_declarations_were_normalised_not_just_found(analysis):
    nq = analysis.declarations[DC.NET_QUANTITY]
    assert nq.norm["value_base"] == 200.0
    assert nq.norm["is_canonical"] is False
    assert nq.norm["unit_raw"] == "gms"

    mrp = analysis.declarations[DC.RETAIL_SALE_PRICE]
    assert mrp.norm["value"] == 45.0
    assert mrp.norm["count"] == 1        # unit price must not be counted as a second MRP
    assert mrp.norm["has_tax_clause"] is False


def test_packing_date_drives_the_rule_version(analysis):
    assert str(analysis.scan.packing_date) == "2026-03-01"
    # post-2022 packing date, so the unit price rules are in force
    assert any(f.rule_id == "LMPCR.R6.1.F.UNIT_PRICE_PRESENT" for f in analysis.findings)


def test_gtin_conflict_is_flagged_without_asserting_an_offence(analysis):
    finding = next(f for f in analysis.findings if f.rule_id == "FORENSIC.GTIN.PREFIX")
    assert finding.verdict is Verdict.INCONCLUSIVE
    assert finding.penalty_ref is None
    assert "China" in finding.detail


def test_every_finding_carries_full_provenance(analysis):
    for finding in analysis.findings:
        assert finding.rule_id and finding.rules_version
        assert finding.citation.clause
        assert finding.message
        assert finding.finding_id.startswith("F-")


def test_no_violation_is_recorded_below_its_required_tier(analysis):
    for finding in analysis.violations:
        if finding.measured is not None:
            assert finding.measured.tier.satisfies(AssuranceTier.B)


def test_report_artefacts_are_produced(analysis, tmp_path):
    pdf_path = pdf.write(analysis, tmp_path / "report.pdf")
    assert pdf_path.exists() and pdf_path.stat().st_size > 4000
    assert pdf_path.with_suffix(".json").exists()

    docx_path = docx_export.write(analysis, tmp_path / "report.docx")
    assert docx_path.exists() and docx_path.stat().st_size > 4000

    notice = docx_export.write_notice(analysis, tmp_path / "notice.docx",
                                      premises="Test Stores")
    assert notice.exists()


def test_report_sections_cover_the_required_structure(analysis):
    titles = [s.title for s in render.build(analysis)]
    for required in ("Inspection particulars", "Declarations extracted",
                     "Applicability and exemptions", "Findings",
                     "Measurement annexe", "Adjudication trail", "Evidence integrity"):
        assert required in titles


def test_headline_counts_match_the_findings(analysis):
    assert str(len(analysis.violations)) in render.headline(analysis)
