"""Measured capture-quality indicators, with conservative, actionable advice.

These image statistics are diagnostic heuristics, not a calibrated score or
proof that an unobserved declaration is absent. Occlusion cannot be established
from one photograph without knowing what the package should show.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def assess_quality(image, *, text_boxes: list | None = None, crop: list[float] | None = None) -> dict:
    """Assess pixels and optional OCR/capture geometry without inferring hidden content."""
    if isinstance(image, (str, Path)):
        image = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or not getattr(image, "size", 0):
        raise ValueError("The image could not be decoded for quality assessment")
    height, width = image.shape[:2]
    ratio = min(1.0, 1200 / max(height, width))
    sample = cv2.resize(image, None, fx=ratio, fy=ratio) if ratio < 1 else image
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY) if sample.ndim == 3 else sample
    p05, p50, p95 = np.percentile(gray, (1, 50, 99))
    laplacian = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # JPEG ringing and sensor noise can make an unfocused photograph appear
    # "sharp" to raw Laplacian variance. Measure after gentle denoising too.
    denoised_laplacian = float(cv2.Laplacian(cv2.GaussianBlur(gray, (0, 0), .9), cv2.CV_64F).var())
    dark = float(np.mean(gray < 35))
    bright = float(np.mean(gray > 247))
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float(np.mean(edges > 0))
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    count, _, ink_stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    fragments = sum(3 <= int(s[4]) <= 18 and max(s[2], s[3]) <= 7 for s in ink_stats[1:])
    fragmented = fragments > 100 and fragments / max(count - 1, 1) > .55
    # A blank white label is not evidence of specular glare. Require a local
    # saturated patch surrounded by a materially darker textured scene.
    mask = (gray > 249).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    patch = max((int(s[4]) for s in stats[1:n]), default=0) / gray.size
    glare = 0.015 < patch < 0.45 and p50 < 205 and edge_density > 0.015
    tiles = [float(np.median(t)) for row in np.array_split(gray, 4) for t in np.array_split(row, 4, axis=1)]
    lighting_range = max(tiles) - min(tiles)
    issues: list[dict] = []

    def issue(code, message, action, *, severity="warning", bbox=None):
        issues.append({"code": code, "severity": severity, "message": message,
                       "action": action, "bbox": bbox})

    if min(width, height) < 600:
        issue("low_resolution", f"The shortest image side is {min(width, height)} pixels.",
              "Move closer to the printed declarations and capture a full-resolution image.")
    if ((laplacian < 35 and edge_density < 0.025)
            or (denoised_laplacian < 20 and edge_density < .008)):
        issue("blur_or_low_detail", "The image has very little sharp detail; blur or an unprinted area may be responsible.",
              "Focus on the printed label, hold the camera steady and capture a close-up.")
    if p95 - p05 < 45:
        issue("low_contrast", "Foreground and background have little brightness separation.",
              "Use diffuse side lighting and bring the camera closer to faint ink markings.")
    if p50 > 245 and bright > 0.65 and (p95 - p05 < 45 or edge_density < 0.008):
        issue("overexposed_or_blank", "Most of the image is nearly white; overexposure or an unprinted area may be responsible.",
              "Reduce exposure or direct light, then capture the printed label with visible ink detail.")
    if p50 < 65 or dark > 0.65:
        issue("underexposed", "Much of the image is dark.",
              "Add diffuse light and keep your hand and camera shadow off the label.")
    if lighting_range > 130 and dark > 0.1:
        issue("uneven_lighting", "Brightness varies strongly across the image; shadows may hide text.",
              "Light both sides of the label evenly and photograph the shadowed region again.")
    if glare:
        issue("possible_glare", "A locally saturated bright patch may obscure printing.",
              "Switch off direct flash and tilt the light or package slightly until the reflection moves away.")
    if fragmented:
        issue("fragmented_ink", "Many disconnected small ink marks suggest dot-matrix printing, damaged characters or image noise.",
              "Capture a sharper close-up of the stamped MRP/date/batch region and verify each digit against the photograph.")
    boxes = text_boxes or []
    tiny = [b for b in boxes if min(b[2] - b[0], b[3] - b[1]) < 14]
    clipped = [b for b in boxes if b[0] < 3 or b[1] < 3 or b[2] > width - 3 or b[3] > height - 3]
    if tiny:
        issue("tiny_text", f"{len(tiny)} detected text region(s) are under 14 pixels high or wide.",
              "Capture a closer image of this text while keeping the declaration keyword and value together.", bbox=list(tiny[0]))
    if clipped:
        issue("possible_cropped_text", "Detected text touches the image edge and may be cut off.",
              "Include a margin around the entire declaration and recapture this edge.", bbox=list(clipped[0]))
    selected_fraction = None
    if crop is not None:
        try:
            left, top, right, bottom = (float(value) for value in crop)
            selected_fraction = max(0.0, right - left) * max(0.0, bottom - top)
        except (TypeError, ValueError):
            selected_fraction = None
        if selected_fraction is not None and selected_fraction < 0.25:
            issue("tight_crop_context", f"This edit keeps {selected_fraction:.0%} of the original image, so package context may be missing.",
                  "Keep this close-up and add a full-panel photograph showing the complete declaration and its location.")
    quad = detect_panel_quad(sample)
    perspective = None
    if quad is not None:
        lengths = np.linalg.norm(quad - np.roll(quad, -1, axis=0), axis=1)
        perspective = float(max(max(lengths[0], lengths[2]) / max(1, min(lengths[0], lengths[2])),
                                max(lengths[1], lengths[3]) / max(1, min(lengths[1], lengths[3]))))
        if perspective > 1.3:
            issue("possible_perspective", "Opposite edges of a detected rectangular region differ substantially.",
                  "Face the label square-on; use several close-ups for curved packaging.")
    return {
        "method": "capture statistics and detected text geometry; heuristic, not calibrated",
        "width": width, "height": height,
        "metrics": {"laplacian_variance": round(laplacian, 2), "contrast_p99_p01": round(float(p95 - p05), 2),
                    "denoised_laplacian_variance": round(denoised_laplacian, 2),
                    "median_brightness": round(float(p50), 2), "dark_fraction": round(dark, 4),
                    "saturated_fraction": round(bright, 4), "edge_density": round(edge_density, 4),
                    "lighting_range": round(lighting_range, 2), "opposite_edge_ratio": perspective,
                    "small_ink_components": int(fragments),
                    "selected_original_fraction": round(selected_fraction, 4) if selected_fraction is not None else None},
        "issues": issues,
        "status": "review" if issues else "no_obvious_quality_issue",
        "requires_rescan": any(i["code"] in {"fragmented_ink", "possible_glare", "blur_or_low_detail", "underexposed", "overexposed_or_blank", "possible_cropped_text"} for i in issues),
        "unassessed": ["occlusion: requires a known view or inspector confirmation",
                       "unseen/cropped declarations outside the frame",
                       "curvature and physical text size without calibration"],
    }


def detect_panel_quad(image):
    """Find a dominant convex quadrilateral; never assume it is the package."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    contours, _ = cv2.findContours(cv2.Canny(gray, 70, 170), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area = gray.size
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        if not 0.15 * area < cv2.contourArea(contour) < 0.95 * area:
            continue
        polygon = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        pts = polygon[:, 0, :].astype(np.float32)
        sums, diffs = pts.sum(axis=1), np.diff(pts, axis=1)[:, 0]
        ordered = np.array([pts[sums.argmin()], pts[diffs.argmin()],
                            pts[sums.argmax()], pts[diffs.argmax()]], dtype=np.float32)
        if len(np.unique(ordered, axis=0)) == 4:
            return ordered
    return None
