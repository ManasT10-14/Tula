"""Reading the label's scale off the package's own barcode.

Rule 7(2) is stated in millimetres and a photograph has none. Almost every
retail package nevertheless carries a printed object whose size a standard
constrains -- its barcode -- and that is enough to bracket the scale without
asking the officer for anything.
"""
from __future__ import annotations

import pytest

from tula.domain.enums import AssuranceTier
from tula.imaging import metrology
from tula.rules import scale_free


def test_the_bracket_is_the_range_the_standard_permits():
    """k=2 spans exactly 80%-200% magnification, and nothing wider or narrower.

    The uncertainty here is a statement about the specification, not a guess
    about this printer, so it has to reproduce the specification exactly.
    """
    width_px = 273.0
    estimate = metrology.from_barcode(width_px, "EAN-13")
    assert estimate is not None
    low = 95 * metrology.NOMINAL_X_MM * metrology.MAGNIFICATION[0] / width_px
    high = 95 * metrology.NOMINAL_X_MM * metrology.MAGNIFICATION[1] / width_px
    assert estimate.mm_per_px - 2 * estimate.sigma == pytest.approx(low, rel=1e-6)
    assert estimate.mm_per_px + 2 * estimate.sigma == pytest.approx(high, rel=1e-6)


def test_the_centre_assumes_nothing_about_where_in_the_range_this_printer_sits():
    """Midpoint, because nothing is known beyond "somewhere in this interval"."""
    estimate = metrology.from_barcode(273.0, "EAN-13")
    low = 95 * metrology.NOMINAL_X_MM * metrology.MAGNIFICATION[0] / 273.0
    high = 95 * metrology.NOMINAL_X_MM * metrology.MAGNIFICATION[1] / 273.0
    assert estimate.mm_per_px == pytest.approx((low + high) / 2, rel=1e-9)


def test_a_wider_printed_symbol_means_a_finer_scale():
    near = metrology.from_barcode(600.0, "EAN-13")
    far = metrology.from_barcode(150.0, "EAN-13")
    assert near.mm_per_px < far.mm_per_px


def test_eight_digit_symbols_use_their_own_module_count():
    """EAN-8 is 67 modules, not 95. Using 95 would report a smaller package."""
    thirteen = metrology.from_barcode(273.0, "EAN-13")
    eight = metrology.from_barcode(273.0, "EAN-8")
    assert eight.mm_per_px < thirteen.mm_per_px
    assert eight.mm_per_px / thirteen.mm_per_px == pytest.approx(67 / 95, rel=1e-9)


def test_it_never_claims_more_than_tier_c():
    """A bracket from a printing standard cannot sustain a violation.

    The height rules require Tier B, and the engine's tier gate is what stops
    this from convicting anyone. That gate is only correct if this estimate is
    honest about its own standing.
    """
    assert metrology.from_barcode(273.0, "EAN-13").tier is AssuranceTier.C


@pytest.mark.parametrize("width,symbology", [
    (0.0, "EAN-13"), (-5.0, "EAN-13"), (273.0, "CODE-128"), (273.0, "QRCode"),
])
def test_unusable_input_yields_no_estimate(width, symbology):
    """A symbology with no fixed module count is not a ruler."""
    assert metrology.from_barcode(width, symbology) is None


def test_it_fuses_with_an_independent_prior_rather_than_replacing_it():
    """Two independent Tier C opinions are worth more than either alone."""
    barcode = metrology.from_barcode(273.0, "EAN-13")
    prior = metrology.from_reference(700.0, 120.0, "geometry_prior")
    fused = metrology.fuse([barcode, prior])
    assert fused.sigma < min(barcode.sigma, prior.sigma)


# --------------------------------------------------------------------------
# Narrowing the scale-free sweep
# --------------------------------------------------------------------------

def test_an_overlapping_measured_bracket_narrows_the_sweep():
    prior = (15.0, 600.0)          # nothing known from the declared quantity
    measured = (47.5, 184.5)       # from the barcode
    assert scale_free._intersect(prior, measured) == (47.5, 184.5)


def test_the_tighter_of_two_overlapping_brackets_wins_on_each_side():
    assert scale_free._intersect((70.0, 180.0), (47.5, 184.5)) == (70.0, 180.0)
    assert scale_free._intersect((40.0, 300.0), (47.5, 184.5)) == (47.5, 184.5)


def test_brackets_that_disagree_do_not_produce_a_confident_answer():
    """Non-overlapping means one source is wrong about this package.

    Narrowing to an empty or arbitrary range would manufacture confidence out
    of a contradiction, so the sweep stays wide and can only abstain more.
    """
    prior = (70.0, 180.0)
    assert scale_free._intersect(prior, (300.0, 400.0)) == prior
    assert scale_free._intersect(prior, None) == prior
