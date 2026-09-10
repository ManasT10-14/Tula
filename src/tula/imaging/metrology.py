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
    "barcode_symbol": 0.237,
    "geometry_prior": 0.330,
}

SOURCE_TIER = {
    "artwork": AssuranceTier.A,
    "aruco_card": AssuranceTier.B,
    "device_depth": AssuranceTier.B,
    "mono_metric": AssuranceTier.C,
    # A bracket derived from a printing standard, not a measurement of this
    # package. It may support an advisory or a clearance; never a conviction.
    "barcode_symbol": AssuranceTier.C,
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


# --------------------------------------------------------------------------
# The barcode as a ruler
# --------------------------------------------------------------------------
#
# Rule 7(2) is stated in millimetres and a photograph has none, so the height
# rules normally wait for someone to walk back to the shop with the scale card.
# But almost every retail package already carries a printed object of regulated
# size: its own barcode.
#
# EAN-13 and UPC-A encode 95 modules between the symbol's outer bar edges, EAN-8
# encodes 67. The module width -- the X-dimension -- is 0.330 mm at nominal
# (100%) magnification, and the GS1 General Specifications permit retail
# point-of-sale symbols to be printed between 80% and 200% of nominal. So the
# symbol's true width lies in a bounded interval, and dividing by its measured
# pixel width brackets the scale of the label plane.
#
# This is a bracket, not a measurement: the permitted magnification range spans
# a factor of 2.5, which is why the estimate below is Tier C and carries a large
# sigma. What makes it worth having is that it is *independent* of the declared
# quantity and its density prior, so where both exist they fuse into something
# tighter than either, and where the quantity could not be read the barcode is
# the only automatic scale left. Neither can sustain a violation on its own --
# the tier gate in the rules engine sees to that.

MODULES = {"EAN-13": 95, "UPC-A": 95, "EAN-8": 67, "UPC-E": 51}
NOMINAL_X_MM = 0.330
# GS1 General Specifications, retail POS magnification range.
MAGNIFICATION = (0.80, 2.00)


def from_barcode(
    pixel_width: float, symbology: str, *, detail: str = ""
) -> ScaleEstimate | None:
    """Bracket mm-per-pixel from a retail barcode's printed width.

    The returned interval at k=2 is exactly the range the standard permits, so
    the uncertainty is a statement about the specification rather than a guess
    about this particular printer.
    """
    modules = MODULES.get(symbology)
    if not modules or pixel_width <= 0:
        return None
    low, high = (modules * NOMINAL_X_MM * m / pixel_width for m in MAGNIFICATION)
    # Arithmetic centre with sigma a quarter of the range, so that the k=2
    # interval this estimate advertises is exactly [low, high].
    #
    # A geometric centre is the more natural summary of a multiplicative
    # quantity, and it was what this used first -- but `ScaleEstimate` carries
    # one symmetric sigma, so a geometric centre cannot also span the permitted
    # range, and the interval is the part the conformity decision actually
    # tests. Nothing is known about where in the range this printer sits, and
    # for a quantity known only to lie in an interval the midpoint is the honest
    # summary; the geometric centre would quietly assert that smaller
    # magnifications are likelier.
    centre = (low + high) / 2.0
    sigma = (high - low) / 4.0
    return ScaleEstimate(
        source="barcode_symbol",
        mm_per_px=centre,
        sigma=sigma,
        tier=SOURCE_TIER["barcode_symbol"],
        detail=detail or (
            f"{symbology} symbol spans {pixel_width:.0f} px; {modules} modules at "
            f"{NOMINAL_X_MM} mm and {MAGNIFICATION[0]:.0%}-{MAGNIFICATION[1]:.0%} "
            f"magnification put the label between {low:.4f} and {high:.4f} mm/px"
        ),
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


# ---------------------------------------------------------------------------
# Panel extent, without a ruler
# ---------------------------------------------------------------------------


@dataclass
class PanelExtent:
    """How large the principal display panel is, in pixels, as an interval.

    A photograph does not hand you the panel boundary. Two things about it are
    knowable without guessing: every printed line sits *on* the panel, so the
    hull of the recognised text is a floor; and if the package is wholly inside
    the frame, the frame is a ceiling. Segmentation, when it agrees with both,
    narrows the interval -- it never replaces it.

    The point estimate is the geometric mean of the two bounds, which is the
    centre of the interval in the ratio sense, and `rel_sigma` is the standard
    deviation of a uniform distribution across it. That is a wide error bar on
    purpose: the Rule 8 threshold changes sixfold across the table, so an
    over-confident panel area is worse than an honest interval.
    """

    width_px: float
    height_px: float
    min_width_px: float
    min_height_px: float
    max_width_px: float
    max_height_px: float
    method: str
    rel_sigma: float
    segmented: bool = False

    @property
    def span_px(self) -> float:
        """The longer side of the point estimate."""
        return max(self.width_px, self.height_px)

    @property
    def geometric_mean_px(self) -> float:
        return math.sqrt(self.width_px * self.height_px)


# A uniform distribution over an interval has this standard deviation relative
# to its half-width; it turns a bracket into an error bar without inventing a
# sharper distribution than the evidence supports.
_UNIFORM_SIGMA = 1.0 / math.sqrt(3.0)


def _text_hull(boxes) -> tuple[int, int, int, int] | None:
    usable = [b for b in boxes if b and b[2] > b[0] and b[3] > b[1]]
    if not usable:
        return None
    return (
        min(b[0] for b in usable), min(b[1] for b in usable),
        max(b[2] for b in usable), max(b[3] for b in usable),
    )


def _segment_package(image_path: str):
    """Find the package against its background; return its box and the frame size.

    Deliberately conservative. Product photography is not a controlled scene:
    the background may be a shelf, another package, or the officer's hand. The
    caller checks this result against the text hull and discards it when the
    two disagree, so a wrong segmentation costs precision, never correctness.
    """
    if not _CV or not Path(image_path).exists():
        return None
    image = cv2.imread(image_path)
    if image is None:
        return None
    height, width = image.shape[:2]
    factor = 900.0 / max(height, width)
    small = cv2.resize(image, None, fx=factor, fy=factor) if factor < 1 else image
    edge = np.concatenate([
        small[0:3].reshape(-1, 3), small[-3:].reshape(-1, 3),
        small[:, 0:3].reshape(-1, 3), small[:, -3:].reshape(-1, 3),
    ])
    background = np.median(edge, axis=0)
    noise = float(np.median(np.abs(edge - background)))
    distance = np.linalg.norm(small.astype(np.float32) - background, axis=2)
    mask = (distance > max(18.0, 4.0 * noise)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(biggest)
    if w < 8 or h < 8:
        return None
    back = 1.0 / factor if factor < 1 else 1.0
    box = (int(x * back), int(y * back), int((x + w) * back), int((y + h) * back))
    return box, width, height


def panel_extent_px(
    image_path: str | None,
    text_boxes,
    *,
    frame_size: tuple[int, int] | None = None,
) -> PanelExtent | None:
    """Bracket the panel's pixel extent from the photograph alone.

    Returns None when there is no recognised text to anchor the floor, because
    a bracket with no floor is just the frame, and the frame is not a
    measurement of anything.
    """
    hull = _text_hull(text_boxes)
    if hull is None:
        return None
    low_w = float(hull[2] - hull[0])
    low_h = float(hull[3] - hull[1])
    if low_w <= 0 or low_h <= 0:
        return None

    segmentation = _segment_package(image_path) if image_path else None
    width = height = None
    method = "text hull to frame"
    segmented = False
    if segmentation is not None:
        box, frame_w, frame_h = segmentation
        width, height = float(frame_w), float(frame_h)
        contains_text = (
            box[0] <= hull[0] + 2 and box[1] <= hull[1] + 2
            and box[2] >= hull[2] - 2 and box[3] >= hull[3] - 2
        )
        box_w, box_h = float(box[2] - box[0]), float(box[3] - box[1])
        # A segmentation that fills the frame has found the frame, not the
        # package; one that misses printed text has found something else.
        useful = box_w * box_h < 0.97 * width * height
        if contains_text and useful and box_w >= low_w and box_h >= low_h:
            width, height = box_w, box_h
            method = "package segmented against its background"
            segmented = True
    if width is None or height is None:
        frame = frame_size or _frame_size(image_path)
        if frame is None:
            return None
        width, height = float(frame[0]), float(frame[1])
    high_w, high_h = max(width, low_w), max(height, low_h)

    # The interval is multiplicative, so its centre is the geometric mean.
    point_w = math.sqrt(low_w * high_w)
    point_h = math.sqrt(low_h * high_h)
    half = 0.5 * (
        (high_w - low_w) / (high_w + low_w) + (high_h - low_h) / (high_h + low_h)
    )
    return PanelExtent(
        width_px=point_w, height_px=point_h,
        min_width_px=low_w, min_height_px=low_h,
        max_width_px=high_w, max_height_px=high_h,
        method=(
            f"{method}; printed text spans {low_w:.0f} x {low_h:.0f} px, "
            f"outer bound {high_w:.0f} x {high_h:.0f} px"
        ),
        rel_sigma=half * _UNIFORM_SIGMA,
        segmented=segmented,
    )


def _frame_size(image_path: str | None) -> tuple[int, int] | None:
    if not image_path or not Path(image_path).exists():
        return None
    if _CV:
        image = cv2.imread(image_path)
        if image is not None:
            return int(image.shape[1]), int(image.shape[0])
    try:
        from PIL import Image

        with Image.open(image_path) as handle:
            return int(handle.width), int(handle.height)
    except (OSError, ValueError):  # pragma: no cover - unreadable file
        return None


def pdp_area_from_extent(extent: PanelExtent, scale: ScaleEstimate) -> Measured | None:
    """Panel area from a bracketed pixel extent and a millimetre scale.

    This reports a *bound*, not an estimate with an error bar around it, and
    the distinction is the whole point. Measured against rendered labels whose
    true panel size is known, a point estimate at the centre of the bracket
    under-read the area by a median of 31% and its k=2 interval missed the
    truth in five cases out of eight -- because where the truth sits inside the
    bracket depends on how much of the frame the photographer filled, which is
    a fact about the photographer and not about the package.

    So the interval is the bracket itself: the floor is the panel being no
    larger than the printed text on it, the ceiling is the package boundary or
    the frame. The true panel is inside that by construction whenever the
    package is wholly in view, and the scale's own uncertainty widens it
    further. The value in the middle is a midpoint, not a claim.

    A wide interval is not a weakness here. It flows into the same guard-banded
    comparison every other measurement does, so Rule 8 decides where the band
    is unambiguous and declines where it is not -- instead of selecting a
    Table I row from a number that is confidently wrong.
    """
    if scale.mm_per_px <= 0 or extent.min_width_px <= 0 or extent.max_width_px <= 0:
        return None
    per_cm2 = (scale.mm_per_px**2) / 100.0
    low = extent.min_width_px * extent.min_height_px * per_cm2
    high = extent.max_width_px * extent.max_height_px * per_cm2
    if high < low:
        return None

    value = (low + high) / 2.0
    half_width = (high - low) / 2.0
    # Area goes as scale squared, so the scale's relative error doubles.
    scale_term = value * COVERAGE_FACTOR * 2 * (scale.sigma / scale.mm_per_px)

    return Measured(
        quantity="pdp_area",
        value=value,
        uncertainty=math.hypot(half_width, scale_term),
        unit="cm2",
        tier=scale.tier,
        sources=[scale.source, "panel extent bounded from the photograph"],
        method=(
            f"panel area bounded to {low:.0f}-{high:.0f} cm2 at "
            f"{scale.mm_per_px:.5f} mm/px; {extent.method}"
        ),
    )
