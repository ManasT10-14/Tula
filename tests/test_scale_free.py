"""Screening a height rule when no scale reference reached the camera.

The claim under test is narrow and worth stating plainly: where Rule 8 fails
at every package size the declared quantity allows, the shortfall does not
depend on the scale, and an inspector can be told so without anyone measuring
the package. Where the answer does depend on the scale, the screen must stay
silent -- an inconclusive verdict is the correct output there, and replacing
it with a guess would be worse than saying nothing.
"""

from __future__ import annotations

import shutil

import pytest

from tula.analyse import Capture, analyse
from tula.domain.enums import AssuranceTier, Verdict
from tula.labgen import LabelSpec, render

SPEC = {"panel_w_mm": 100.0, "panel_h_mm": 150.0, "px_per_mm": 26.0,
        "net_qty_value": "500", "net_qty_unit": "g"}


@pytest.fixture(scope="module")
def unscaled(tmp_path_factory):
    """Render labels, then strip every scale reference from the capture.

    The rendered sidecar is what gives a bench label its Tier B scale. Deleting
    it leaves exactly what an officer's phone produces: pixels and nothing else.
    """
    root = tmp_path_factory.mktemp("scale-free")

    def _run(net_qty_mm):
        stem = f"h{int(net_qty_mm * 10)}"
        rendered = render(LabelSpec(net_qty_mm=net_qty_mm, **SPEC), root, stem)
        bare = root / f"{stem}-bare.png"
        shutil.copyfile(rendered.png, bare)
        return analyse([Capture(str(bare))])

    return _run


def _screen(result):
    return [f for f in result.findings if f.rule_id.startswith("SCREEN.SCALE_FREE")]


def test_shortfall_is_reported_when_it_holds_at_every_package_size(unscaled):
    result = unscaled(1.2)
    assert not result.scan.scales or all(
        s.source == "geometry_prior" for s in result.scan.scales
    )
    short = [f for f in _screen(result) if f.verdict is Verdict.ADVISORY]
    assert short, "1.2 mm numerals on a 500 g pack are short at any plausible size"
    assert "never met" in short[0].detail
    assert "text hull" in short[0].detail


def test_the_screen_may_never_convict(unscaled):
    # Its bound rests on a density prior, which is Tier C. Advisory is the
    # strongest standing it may hold, and the statutory finding beside it must
    # stay inconclusive rather than borrowing the screen's confidence.
    result = unscaled(1.2)
    for finding in _screen(result):
        assert finding.verdict is not Verdict.VIOLATION
        assert finding.tier is AssuranceTier.C
    statutory = [f for f in result.findings if f.rule_id.startswith("LMPCR.R8")]
    assert statutory and all(f.verdict is Verdict.INCONCLUSIVE for f in statutory)


def test_a_comfortable_margin_spares_a_second_visit(unscaled):
    result = unscaled(6.0)
    cleared = [f for f in _screen(result) if f.verdict is Verdict.PASS]
    assert cleared, "6 mm numerals clear the table at every size a 500 g pack can be"
    assert "outer bound" in cleared[0].detail
    assert "not a legal clearance" in cleared[0].citation.text.lower()


def test_the_screen_is_silent_where_the_answer_needs_a_scale(unscaled):
    # 2.5 mm against a 2.5 mm limit is the case the guard band exists for.
    assert not _screen(unscaled(2.5))


def test_span_bounds_widen_when_no_quantity_was_read():
    from tula.rules.scale_free import DEFAULT_SPAN_MM, span_bounds_mm

    assert span_bounds_mm(None, None) == DEFAULT_SPAN_MM
    assert span_bounds_mm(500.0, "kg") == DEFAULT_SPAN_MM


def test_span_bounds_floor_is_geometric_not_assumed():
    from tula.rules.scale_free import span_bounds_mm

    low, high = span_bounds_mm(500.0, "g")
    # A face holding 500 cm3 of contents cannot have a mean side below the
    # cube root of that volume, whatever shape the package takes.
    assert low == pytest.approx(500 ** (1 / 3) * 10, rel=1e-6)
    assert low < high
    bigger = span_bounds_mm(2000.0, "g")
    assert bigger[0] > low and bigger[1] > high
