"""Bounded OCR variants and transformations back to original evidence pixels."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import cv2
import numpy as np

from ..imaging.quality import detect_panel_quad

CRITICAL = re.compile(r"m\.?\s*r\.?\s*p|exp|mfg|mfd|pkd|batch|best\s*before|use\s*by|net\s*(?:wt|qty|weight)", re.IGNORECASE)


@dataclass
class Variant:
    name: str
    image: np.ndarray
    to_original: np.ndarray


def fit_variant(image, name="original", max_side=1800, upscale=1.0, transform=None):
    h, w = image.shape[:2]
    factor = min(upscale, max_side / max(h, w))
    resized = cv2.resize(image, (max(1, round(w * factor)), max(1, round(h * factor))),
                         interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
    sy, sx = resized.shape[0] / h, resized.shape[1] / w
    inverse = np.diag([1 / sx, 1 / sy, 1.0])
    return Variant(name, resized, (transform if transform is not None else np.eye(3)) @ inverse)


def rotate_variant(base, degrees):
    h, w = base.image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    c, s = abs(matrix[0, 0]), abs(matrix[0, 1])
    nw, nh = math.ceil(h * s + w * c), math.ceil(h * c + w * s)
    matrix[0, 2] += (nw - w) / 2
    matrix[1, 2] += (nh - h) / 2
    warped = cv2.warpAffine(base.image, matrix, (nw, nh), borderValue=(255, 255, 255))
    inverse = np.linalg.inv(np.vstack([matrix, [0, 0, 1]]))
    return fit_variant(warped, f"rotate_{degrees:g}", transform=base.to_original @ inverse)


def contrast_variant(base):
    gray = cv2.cvtColor(base.image, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    sharp = cv2.addWeighted(enhanced, 1.5, cv2.GaussianBlur(enhanced, (0, 0), 1), -0.5, 0)
    return fit_variant(cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR), "clahe_upscale", upscale=2,
                       transform=base.to_original)


def reconnect_ink_variant(image):
    """A small native-pixel bridge for detected dot-matrix fragmentation.

    Applying a 2x3 close before upscaling bridges a one-pixel printing gap;
    increasing a kernel after resizing may instead leave those gaps intact.
    The original image remains untouched and disagreements are retained.
    """
    base = fit_variant(image, max_side=1800)
    gray = cv2.cvtColor(base.image, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    joined = 255 - cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((2, 3), np.uint8))
    return fit_variant(cv2.cvtColor(joined, cv2.COLOR_GRAY2BGR), "ink_reconnect",
                       upscale=1.5, max_side=1800, transform=base.to_original)


def reconnect_ink_regions(image, lines):
    """Locate porous numeric printing even when dots form connected letters.

    Counting tiny components misses touching dot-matrix strokes. Measure how
    much a tiny close fills gaps inside actual detected numeric text instead.
    This only schedules another recognizer pass; it does not certify the ink
    or any interpreted number. At most 16 bounded regions are measured.
    """
    base = fit_variant(image, max_side=1800)
    gray = cv2.cvtColor(base.image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    sx, sy = width / image.shape[1], height / image.shape[0]
    eligible = [line for line in lines if sum(c.isdigit() for c in line.text) >= 2
                and line.confidence >= .4 and line.width_px >= line.box_height_px]
    regions = []
    for line in eligible[:16]:
        x0, y0, x1, y1 = line.bbox
        box = (max(0, int(x0 * sx) - 4), max(0, int(y0 * sy) - 4),
               min(width, math.ceil(x1 * sx) + 4), min(height, math.ceil(y1 * sy) + 4))
        if not 12 <= box[3] - box[1] <= 160 or box[2] - box[0] < 30:
            continue
        crop = gray[box[1]:box[3], box[0]:box[2]]
        low, high = np.percentile(crop, (5, 95))
        if high - low < 50:
            continue
        _, ink = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        area = np.count_nonzero(ink)
        if not .015 <= area / ink.size <= .55:
            continue
        closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((2, 3), np.uint8))
        # Use net area, not changed-pixel count: an even kernel can translate
        # edges, which must not be mistaken for newly filled printing gaps.
        gain = (np.count_nonzero(closed) - area) / max(1, area)
        if .25 <= gain <= 1.5:
            regions.append({"bbox": list(line.bbox), "gap_fill_fraction": round(float(gain), 4),
                            "method": "net ink area increase after a 2x3 close; uncalibrated"})
    return regions


def perspective_variant(base):
    quad = detect_panel_quad(base.image)
    if quad is None:
        return None
    lengths = np.linalg.norm(quad - np.roll(quad, -1, axis=0), axis=1)
    ratio = max(max(lengths[0], lengths[2]) / max(1, min(lengths[0], lengths[2])),
                max(lengths[1], lengths[3]) / max(1, min(lengths[1], lengths[3])))
    if ratio < 1.12 or ratio > 2.5:
        return None
    w, h = round(max(lengths[0], lengths[2])), round(max(lengths[1], lengths[3]))
    if min(w, h) < 40:
        return None
    target = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    matrix = cv2.getPerspectiveTransform(quad, target)
    image = cv2.warpPerspective(base.image, matrix, (w, h), borderValue=(255, 255, 255))
    return fit_variant(image, "perspective_region", transform=base.to_original @ np.linalg.inv(matrix))


def targeted_variants(image, lines):
    """Try faint/stamped declarations at native resolution, including context.

    At most two regions and two alternatives each; morphology is OCR-only and
    never modifies the original evidence used for physical measurement.
    """
    h, w = image.shape[:2]
    candidates = sorted(lines, key=lambda l: (bool(CRITICAL.search(l.text)), l.confidence < 0.9,
                                              -(l.height_px or l.box_height_px)), reverse=True)
    chosen = []
    for line in candidates:
        if not CRITICAL.search(line.text) and line.confidence >= 0.85:
            continue
        x0, y0, x1, y1 = line.bbox
        if any(abs((y0 + y1) / 2 - (b[1] + b[3]) / 2) < max(y1 - y0, b[3] - b[1]) for b in chosen):
            continue
        px, py = max(12, round((x1 - x0) * 0.08)), max(12, round((y1 - y0) * 0.75))
        box = max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py)
        if min(box[2] - box[0], box[3] - box[1]) < 8:
            continue
        chosen.append(box)
        crop = image[box[1]:box[3], box[0]:box[2]]
        transform = np.array([[1, 0, box[0]], [0, 1, box[1]], [0, 0, 1]], dtype=float)
        base = fit_variant(crop, upscale=3, max_side=1600, transform=transform)
        gray = cv2.cvtColor(base.image, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)
        # Bridge dot-matrix gaps mildly; candidate arbitration preserves any
        # number that this operation changes instead of voting it into truth.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        ink = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
        yield Variant(f"inkjet_otsu_{len(chosen)}", cv2.cvtColor(255 - ink, cv2.COLOR_GRAY2BGR), base.to_original)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 31, 9)
        yield Variant(f"inkjet_adaptive_{len(chosen)}", cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), base.to_original)
        if len(chosen) >= 2:
            return


def map_polygon(box, transform, width, height):
    points = cv2.perspectiveTransform(np.asarray(box, dtype=np.float32).reshape(-1, 1, 2),
                                      np.asarray(transform, dtype=np.float64)).reshape(-1, 2)
    points[:, 0] = np.clip(points[:, 0], 0, width)
    points[:, 1] = np.clip(points[:, 1], 0, height)
    return [(float(x), float(y)) for x, y in points]
