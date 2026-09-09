"""Metrology tests.

These cover the claims the pitch actually rests on: that uncertainty is
propagated rather than decorative, that disagreeing sensors widen the interval
instead of biasing it, and that a fused estimate is never dressed up as better
evidence than the source carrying it.
"""

from __future__ import annotations

import math

from tula.domain.enums import AssuranceTier
from tula.domain.models import ScaleEstimate
from tula.imaging import metrology


def _scale(mm_per_px=0.04, sigma=0.0008, source="aruco_card", tier=AssuranceTier.B):
    return ScaleEstimate(source=source, mm_per_px=mm_per_px, sigma=sigma, tier=tier)


def test_from_reference_derives_mm_per_px():
    est = metrology.from_reference(500.0, 25.0, "aruco_card")
    assert est.mm_per_px == 0.05
    assert est.tier is AssuranceTier.B
    assert est.sigma > 0


def test_fusion_is_inverse_variance_weighted():
    precise = _scale(0.0400, 0.0001)
    vague = _scale(0.0500, 0.0100, source="mono_metric", tier=AssuranceTier.C)
    fused = metrology.fuse([precise, vague])
    # the precise source must dominate almost completely
    assert abs(fused.mm_per_px - 0.0400) < 0.0002
    assert fused.sigma <= precise.sigma * 1.05


def test_disagreement_inflates_uncertainty():
    a = _scale(0.0400, 0.0002)
    b = _scale(0.0460, 0.0002, source="device_depth")
    agree = metrology.fuse([a, _scale(0.0401, 0.0002, source="device_depth")])
    disagree = metrology.fuse([a, b])
    assert disagree.sigma > agree.sigma * 3


def test_fused_tier_follows_the_dominant_source():
    strong_b = _scale(0.040, 0.0001, source="aruco_card", tier=AssuranceTier.B)
    weak_c = _scale(0.041, 0.0200, source="mono_metric", tier=AssuranceTier.C)
    assert metrology.fuse([strong_b, weak_c]).tier is AssuranceTier.B

    # when only the weak source is present, the answer must stay Tier C
    assert metrology.fuse([weak_c]).tier is AssuranceTier.C


def test_artwork_scale_is_exact_and_short_circuits():
    artwork = ScaleEstimate(source="artwork", mm_per_px=0.0423, sigma=0.0,
                            tier=AssuranceTier.A)
    fused = metrology.fuse([artwork, _scale(0.05, 0.01, source="mono_metric",
                                            tier=AssuranceTier.C)])
    assert fused.sigma == 0.0
    assert fused.tier is AssuranceTier.A
    assert fused.mm_per_px == 0.0423


def test_cap_height_conversion_propagates_scale_and_edge_error():
    metrics = metrology.GlyphMetrics(cap_height_px=34.7, sample_count=3,
                                     method="test", spread_px=0.5)
    scale = _scale(0.0412, 0.0412 * 0.02)  # 2% relative, like device depth
    measured = metrology.to_millimetres(metrics, scale, quantity="net_qty")

    assert abs(measured.value - 1.43) < 0.02
    assert measured.uncertainty > 0
    # expanded at k=2, so the interval must be wider than the 1-sigma terms
    one_sigma = measured.uncertainty / metrology.COVERAGE_FACTOR
    assert one_sigma >= scale.sigma / scale.mm_per_px * measured.value * 0.9
    assert measured.lower < measured.value < measured.upper


def test_tilt_correction_increases_measured_height():
    metrics = metrology.GlyphMetrics(cap_height_px=30.0, sample_count=1, method="t")
    scale = _scale()
    flat = metrology.to_millimetres(metrics, scale, quantity="q", tilt_deg=0)
    tilted = metrology.to_millimetres(metrics, scale, quantity="q", tilt_deg=25)
    # a 25 degree tilt foreshortens the glyph, so the true height is larger
    assert tilted.value > flat.value
    assert abs(tilted.value - flat.value / math.cos(math.radians(25))) < 1e-6


def test_pdp_area_relative_error_doubles_with_scale():
    scale = _scale(0.04, 0.04 * 0.05)  # 5% relative
    area = metrology.pdp_area_cm2(3000, 4500, scale)
    assert area.unit == "cm2"
    relative = (area.uncertainty / metrology.COVERAGE_FACTOR) / area.value
    # area goes as scale squared -> ~10% relative, not 5%
    assert 0.095 < relative < 0.11


def test_geometry_prior_is_deliberately_imprecise():
    prior = metrology.from_geometry_prior(200.0, "g", 800.0)
    assert prior.tier is AssuranceTier.C
    assert prior.sigma / prior.mm_per_px > 0.3


def test_no_sources_means_no_answer():
    assert metrology.fuse([]) is None


# ---------------------------------------------------------------------------
# Panel extent
# ---------------------------------------------------------------------------


def _photo(tmp_path, width, height, box=None):
    """A plain frame, optionally with a lighter package rectangle drawn on it."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (20, 20, 24))
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill=(238, 238, 232))
    path = tmp_path / "frame.png"
    image.save(path)
    return str(path)


def test_panel_extent_brackets_the_panel_between_text_and_frame(tmp_path):
    path = _photo(tmp_path, 1000, 800)
    extent = metrology.panel_extent_px(path, [(200, 150, 700, 600)])
    assert extent is not None
    # The hull of the printed text is the floor, the frame the ceiling, and the
    # point estimate lives strictly between them.
    assert extent.min_width_px == 500 and extent.min_height_px == 450
    assert extent.max_width_px == 1000 and extent.max_height_px == 800
    assert 500 < extent.width_px < 1000
    assert extent.rel_sigma > 0


def test_panel_extent_narrows_when_the_package_separates_from_its_background(tmp_path):
    path = _photo(tmp_path, 1000, 800, box=(120, 100, 820, 700))
    extent = metrology.panel_extent_px(path, [(200, 150, 700, 600)])
    assert extent is not None and extent.segmented
    # Segmentation may only tighten the ceiling, never breach the floor.
    assert extent.max_width_px < 1000
    assert extent.max_width_px >= extent.min_width_px


def test_panel_extent_needs_text_to_stand_on(tmp_path):
    assert metrology.panel_extent_px(_photo(tmp_path, 400, 400), []) is None


def test_bracketed_panel_area_reports_a_bound_not_an_estimate(tmp_path):
    # Measured against rendered labels, a point estimate at the centre of the
    # bracket under-read the area by a median of 31% and its interval missed
    # the truth five times in eight. The interval must therefore be the
    # bracket, so any panel between the printed text and the frame is inside it.
    path = _photo(tmp_path, 1000, 800)
    extent = metrology.panel_extent_px(path, [(200, 150, 700, 600)])
    scale = _scale(sigma=0.0)
    area = metrology.pdp_area_from_extent(extent, scale)
    per_cm2 = (scale.mm_per_px**2) / 100.0
    floor = extent.min_width_px * extent.min_height_px * per_cm2
    ceiling = extent.max_width_px * extent.max_height_px * per_cm2
    assert area.lower <= floor and ceiling <= area.upper
    assert "bounded to" in area.method and "printed text spans" in area.method


def test_a_tighter_bracket_gives_a_tighter_area(tmp_path):
    path = _photo(tmp_path, 1000, 800)
    scale = _scale()
    loose = metrology.pdp_area_from_extent(
        metrology.panel_extent_px(path, [(400, 320, 600, 480)]), scale
    )
    tight = metrology.pdp_area_from_extent(
        metrology.panel_extent_px(path, [(20, 16, 980, 784)]), scale
    )
    # Text that nearly fills the frame leaves almost nowhere for the panel to
    # hide; text in the middle of a large frame leaves a great deal.
    assert tight.uncertainty / tight.value < loose.uncertainty / loose.value
