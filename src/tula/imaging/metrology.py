"""Millimetres from pixels, with an honest error bar.

Rule 8 is stated in millimetres of physical glyph height. A photograph has no
intrinsic scale, so every font-size verdict in this system reduces to one
number -- `mm_per_px` at the label plane -- and to being truthful about how
well we know it.

Four independent sources can supply that number. They are fused by
inverse-variance weighting, disagreement inflates the result's uncertainty
rather than being hidden, and the assurance tier of the fused estimate is the
*weakest* tier that materially contributed. Nothing here ever returns a bare
float: the caller gets a `Measured` whose interval is what the conformity
decision actually tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..domain.enums import AssuranceTier
from ..domain.models import Measured, ScaleEstimate

try:  # optional: the fixture path measures without ever touching an image
    import cv2
    import numpy as np

    _CV = True
except (ImportError, OSError):  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _CV = False


# Typical 1-sigma for each source, in relative terms. Calibrate these against
# the calliper reference set before any pilot -- they are the difference
# between a credible error bar and a decorative one.
SOURCE_SIGMA_REL = {
    "artwork": 0.0,
    "aruco_card": 0.004,
    "device_depth": 0.020,
    "mono_metric": 0.090,
    "geometry_prior": 0.330,
}

SOURCE_TIER = {
    "artwork": AssuranceTier.A,
    "aruco_card": AssuranceTier.B,
    "device_depth": AssuranceTier.B,
    "mono_metric": AssuranceTier.C,
    "geometry_prior": AssuranceTier.C,
}

# Localisation error when finding a glyph edge in a binarised crop, in pixels.
# Two ~0.5 px edges in quadrature.
EDGE_SIGMA_PX = 0.7

COVERAGE_FACTOR = 2.0  # k=2, ~95% coverage


# ---------------------------------------------------------------------------
# Scale sources
# ---------------------------------------------------------------------------


def from_reference(
    measured_px: float, known_mm: float, source: str, *, detail: str = ""
) -> ScaleEstimate | None:
    """Scale from an object of known physical size in the same plane."""
    if measured_px <= 0 or known_mm <= 0:
        return None
    mm_per_px = known_mm / measured_px
    rel = SOURCE_SIGMA_REL.get(source, 0.05)
    return ScaleEstimate(
        source=source,
        mm_per_px=mm_per_px,
        sigma=mm_per_px * rel,
        tier=SOURCE_TIER.get(source, AssuranceTier.C),
        detail=detail or f"{known_mm:g} mm spans {measured_px:.1f} px",
    )


def from_aruco(image_path: str, marker_mm: float = 25.0) -> ScaleEstimate | None:
    """Detect the printed LM Scale Card and derive mm/px from it.

    The card is a free PDF an inspector prints once and carries. It is the
    cheapest route to Tier B evidence and needs no particular phone.
    """
    if not _CV or not hasattr(cv2, "aruco") or not Path(image_path).exists():
        return None
    image = cv2.imread(image_path)
    if image is None:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    try:
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        corners, ids, _ = detector.detectMarkers(gray)
    except AttributeError:  # older OpenCV API
        dictionary = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
        corners, ids, _ = cv2.aruco.detectMarkers(
            gray, dictionary, parameters=cv2.aruco.DetectorParameters_create()
        )
    if ids is None or len(corners) == 0:
        return None

    sides: list[float] = []
    for corner, marker_id in zip(corners, ids.flatten()):
        if int(marker_id) not in (0, 1, 2, 3):
            continue
        pts = corner.reshape(4, 2)
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            sides.append(float(np.linalg.norm(a - b)))
    if not sides:
        return None

    side_px = float(np.median(sides))
    estimate = from_reference(
        side_px, marker_mm, "aruco_card",
        detail=f"{len(corners)} marker(s), median side {side_px:.1f} px = {marker_mm:g} mm",
    )
    # Perspective and unequal marker sizes must increase uncertainty.
    spread = (max(sides) - min(sides)) / (2 * side_px)
    return estimate.model_copy(update={"sigma": math.hypot(estimate.sigma, estimate.mm_per_px * spread)})


def from_geometry_prior(
    net_quantity_base: float | None,
    unit_base: str | None,
    package_span_px: float,
) -> ScaleEstimate | None:
    """Last-resort scale from the declared quantity and a density prior.

    Deliberately given a large sigma. This exists to cross-check the other
    sources and to keep a Tier C advisory path alive when nothing else is
    available -- never to sustain a violation on its own.
    """
    if not net_quantity_base or package_span_px <= 0 or unit_base not in ("g", "ml"):
        return None
    # Bulk density of typical dry packaged goods, g/cm3. Liquids ~1.0.
    density = 1.0 if unit_base == "ml" else 0.55
    volume_cm3 = net_quantity_base / density
    # A package is not a cube; the longest face spans roughly 1.6x the cube root.
    span_mm = (volume_cm3 ** (1 / 3)) * 10.0 * 1.6
    return from_reference(
        package_span_px, span_mm, "geometry_prior",
        detail=f"{net_quantity_base:g} {unit_base} at {density:g} g/cm3 implies a ~{span_mm:.0f} mm face",
    )


def fuse(estimates: list[ScaleEstimate]) -> ScaleEstimate | None:
    """Inverse-variance fusion with a disagreement penalty.

    If the sources disagree by more than their own uncertainties allow, the
    fused sigma is inflated by the square root of the reduced chi-square. That
    is standard practice when combining measurements, and it means a broken
    sensor widens the error bar instead of quietly biasing the verdict.
    """
    usable = [e for e in estimates if e is not None and e.mm_per_px > 0]
    if not usable:
        return None

    exact = [e for e in usable if e.sigma <= 0]
    if exact:  # artwork: no estimation involved, nothing to fuse
        best = exact[0]
        return ScaleEstimate(
            source=best.source, mm_per_px=best.mm_per_px, sigma=0.0,
            tier=best.tier, detail="exact scale from vector artwork",
        )

    weights = [1.0 / (e.sigma**2) for e in usable]
    total = sum(weights)
    mean = sum(w * e.mm_per_px for w, e in zip(weights, usable)) / total
    sigma = math.sqrt(1.0 / total)

    if len(usable) > 1:
        chi2 = sum(w * (e.mm_per_px - mean) ** 2 for w, e in zip(weights, usable))
        reduced = chi2 / (len(usable) - 1)
        if reduced > 1.0:
            sigma *= math.sqrt(reduced)

    # The fused tier is the tier of whichever source carries the most weight.
    # Inverse-variance weighting already means the most precise source
    # dominates, so this says: the answer is only as trustworthy as the
    # evidence actually holding it up. A Tier C opinion contributing 0.5% of
    # the weight cannot drag a Tier B fusion down, and a Tier C opinion
    # carrying the fusion cannot be dressed up as Tier B.
    dominant = max(zip(weights, usable), key=lambda pair: pair[0])[1]
    tier = dominant.tier

    return ScaleEstimate(
        source="+".join(sorted({e.source for e in usable})),
        mm_per_px=mean,
        sigma=sigma,
        tier=tier,
        detail="; ".join(f"{e.source} {e.mm_per_px:.5f}±{e.sigma:.5f}" for e in usable),
    )


# ---------------------------------------------------------------------------
# Glyph measurement
# ---------------------------------------------------------------------------


@dataclass
class GlyphMetrics:
    cap_height_px: float
    sample_count: int
    method: str
    spread_px: float = 0.0


def measure_cap_height_px(
    image_path: str | None,
    bbox: tuple[int, int, int, int] | None,
    *,
    fallback_px: float | None = None,
) -> GlyphMetrics | None:
    """Measure the true cap-height of numerals inside a crop.

    Naive implementations measure the OCR bounding box, which includes leading,
    descenders and detection slack, and over-report by 20-40%. This works on
    connected components instead, and takes the 5th-95th percentile of their
    vertical extent so a stray comma, decimal point or speckle cannot set the
    answer.
    """
    if _CV and image_path and bbox and Path(image_path).exists():
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if image is not None:
            x0, y0, x1, y1 = bbox
            pad = max(2, int(0.08 * (y1 - y0)))
            x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
            x1 = min(image.shape[1], x1 + pad)
            y1 = min(image.shape[0], y1 + pad)
            crop = image[y0:y1, x0:x1]
            if crop.size:
                if crop.shape[0] > crop.shape[1] * 2:
                    crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
                metrics = _components_cap_height(crop)
                if metrics is not None:
                    return metrics

    if fallback_px:
        # Fixture / no-image path: the OCR line height stands in, and the
        # method string says so plainly so the report cannot overstate it.
        return GlyphMetrics(
            cap_height_px=float(fallback_px), sample_count=1,
            method="ocr_line_height (no pixel measurement available)",
        )
    return None


def _components_cap_height(crop) -> GlyphMetrics | None:
    blur = cv2.GaussianBlur(crop, (3, 3), 0)
    # Try both polarities: label text is as often light-on-dark as dark-on-light.
    best: GlyphMetrics | None = None
    for invert in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
        _, binary = cv2.threshold(blur, 0, 255, invert | cv2.THRESH_OTSU)
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        heights = []
        crop_h = crop.shape[0]
        for i in range(1, count):
            _x, _y, w, h, area = stats[i]
            if h < max(3, crop_h * 0.30) or h > crop_h * 0.98:
                continue
            if area < 6 or w == 0:
                continue
            ratio = h / float(w)
            if ratio < 0.6 or ratio > 6.0:  # digit-like, not a dot or a rule
                continue
            heights.append(float(h))
        if len(heights) < 1:
            continue
        heights.sort()
        lo = heights[max(0, int(0.05 * (len(heights) - 1)))]
        hi = heights[min(len(heights) - 1, int(0.95 * (len(heights) - 1)))]
        kept = [h for h in heights if lo <= h <= hi] or heights
        # Mixed case OCR lines contain shorter x-height glyphs. Use the upper
        # quartile for cap height and retain the spread as model uncertainty.
        median = kept[min(len(kept) - 1, int(0.75 * len(kept)))]
        # Lowercase x-height is a different glyph population, not variation
        # in cap height. Estimate spread within the upper glyph cluster.
        caps = [h for h in kept if h >= median * 0.8]
        spread = (max(caps) - min(caps)) / 2.0
        candidate = GlyphMetrics(
            cap_height_px=median, sample_count=len(kept),
            method="connected components, upper quartile of trimmed glyph heights",
            spread_px=spread,
        )
        if best is None or candidate.sample_count > best.sample_count:
            best = candidate
    return best


def to_millimetres(
    metrics: GlyphMetrics,
    scale: ScaleEstimate,
    *,
    quantity: str,
    tilt_deg: float = 0.0,
) -> Measured:
    """Convert a pixel measurement to millimetres, propagating every error term.

    Contributions, added in quadrature:
      * scale uncertainty, which scales with the measurement itself;
      * edge localisation in the binarised crop;
      * spread across the sampled glyphs;
      * out-of-plane tilt, which shortens apparent height by cos(theta).
    """
    if not math.isfinite(tilt_deg) or abs(tilt_deg) >= 75:
        raise ValueError("Tilt must be finite and within (-75, 75) degrees")
    cos = math.cos(math.radians(tilt_deg))
    px = metrics.cap_height_px / cos
    value = px * scale.mm_per_px

    rel_scale = (scale.sigma / scale.mm_per_px) if scale.mm_per_px else 0.0
    sigma_scale = value * rel_scale
    sigma_edge = EDGE_SIGMA_PX * scale.mm_per_px / cos
    sigma_spread = (
        metrics.spread_px * scale.mm_per_px / cos
    )
    sigma = math.sqrt(sigma_scale**2 + sigma_edge**2 + sigma_spread**2)
    tier = scale.tier
    if metrics.method.startswith("ocr_line_height"):
        # Detection boxes are an uncalibrated proxy for glyph outlines.
        tier = AssuranceTier.C
        sigma = math.hypot(sigma, value * 0.20)

    return Measured(
        quantity=quantity,
        value=value,
        uncertainty=COVERAGE_FACTOR * sigma,
        unit="mm",
        tier=tier,
        sources=[scale.source],
        method=(
            f"{metrics.method}; {metrics.cap_height_px:.1f} px at "
            f"{scale.mm_per_px:.5f} mm/px"
            + (f"; tilt {tilt_deg:.0f}deg corrected" if tilt_deg else "")
        ),
    )


# ---------------------------------------------------------------------------
# Principal display panel
# ---------------------------------------------------------------------------


def pdp_area_cm2(
    width_px: float, height_px: float, scale: ScaleEstimate
) -> Measured | None:
    """Area of the principal display panel, with propagated uncertainty.

    The Rule 8 threshold is selected by this number, so its uncertainty feeds
    the band choice -- which is why the rule pack asks for `favour_subject` at
    a band boundary rather than picking a row from a point estimate.
    """
    if width_px <= 0 or height_px <= 0 or scale.mm_per_px <= 0:
        return None

    area_mm2 = width_px * height_px * (scale.mm_per_px**2)
    area_cm2 = area_mm2 / 100.0

    rel_scale = scale.sigma / scale.mm_per_px
    # area goes as scale squared, so its relative error is doubled; edge
    # localisation on each side contributes a further term
    rel_edge = math.sqrt((EDGE_SIGMA_PX / width_px) ** 2 + (EDGE_SIGMA_PX / height_px) ** 2)
    rel = math.sqrt((2 * rel_scale) ** 2 + rel_edge**2)

    return Measured(
        quantity="pdp_area",
        value=area_cm2,
        uncertainty=COVERAGE_FACTOR * area_cm2 * rel,
        unit="cm2",
        tier=scale.tier,
        sources=[scale.source],
        method=f"{width_px:.0f} x {height_px:.0f} px at {scale.mm_per_px:.5f} mm/px",
    )
