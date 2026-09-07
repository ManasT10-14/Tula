"""Local, orientation-aware associations; never concatenate unrelated panels.

Geometry is a heuristic, not proof that two detections belong together. We retain
the actual source boxes and every plausible value for review.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable

from ..domain.enums import Panel
from ..ocr.base import OcrLine

Located = tuple[Panel, OcrLine]


def same_surface(a: Located, b: Located) -> bool:
    return (a[0] == b[0] and a[1].frame == b[1].frame
            and getattr(a[1], "_capture_index", None) == getattr(b[1], "_capture_index", None))


def source(panel: Panel, line: OcrLine) -> dict:
    dimensions = getattr(line, "_image_size", (0, 0))
    return {"frame": line.frame, "image_index": getattr(line, "_capture_index", None),
            "image_width": dimensions[0], "image_height": dimensions[1],
            "panel": panel.value, "bbox": list(line.bbox), "text": line.text,
            "ocr_confidence": line.confidence}


def sources(panel: Panel, line: OcrLine) -> list[dict]:
    return getattr(line, "_source_spans", None) or [source(panel, line)]


def distance(anchor: OcrLine, candidate: OcrLine) -> float | None:
    """Distance in character heights, measured in the anchor's text axes."""
    angle = float(getattr(anchor, "angle_degrees", 0) or 0)
    if abs(angle) < 1 and anchor.box_height_px > anchor.width_px * 1.6:
        angle = 90.0
    radians = math.radians(angle)
    u = (math.cos(radians), math.sin(radians))
    v = (-u[1], u[0])

    def extents(line: OcrLine, axis: tuple[float, float]):
        x0, y0, x1, y1 = line.bbox
        points = getattr(line, "polygon", None) or [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        projected = [x * axis[0] + y * axis[1] for x, y in points]
        return min(projected), max(projected)

    ax, ay = extents(anchor, u), extents(anchor, v)
    bx, by = extents(candidate, u), extents(candidate, v)
    height = max(4.0, min(ay[1] - ay[0], by[1] - by[0]))
    dy = abs(sum(ay) / 2 - sum(by) / 2)
    gap_x = max(0, bx[0] - ax[1], ax[0] - bx[1])
    gap_y = max(0, by[0] - ay[1], ay[0] - by[1])
    # Values beside the cue may have a slightly uneven baseline.
    if dy <= 1.3 * height and gap_x <= 12 * height and bx[0] >= ax[0] - height:
        return gap_x / height + dy / height
    overlap = min(ax[1], bx[1]) - max(ax[0], bx[0])
    if (gap_y <= 2.5 * height and by[0] >= ay[0] - height
            and (overlap > 0 or abs(bx[0] - ax[0]) <= 2 * height)):
        return 1.0 + gap_y / height + abs(bx[0] - ax[0]) / max(height, ax[1] - ax[0])
    return None


def join(panel: Panel, anchor: OcrLine, value: OcrLine) -> OcrLine:
    # Keep the value box for glyph measurement; the complete evidence comprises
    # two source spans, not a made-up glyph box covering the gap between them.
    joined = copy.copy(value)
    joined.text = anchor.text + " " + value.text
    joined.confidence = min(anchor.confidence, value.confidence)
    joined._source_spans = sources(panel, anchor) + sources(panel, value)
    joined._extraction_method = "spatial_keyword_value"
    joined.review_required = (getattr(anchor, "review_required", False)
                              or getattr(value, "review_required", False))
    return joined


def associated(
    lines: list[Located], cue: re.Pattern, parser: Callable,
    *, other_cues: list[re.Pattern] = (),
) -> list[Located]:
    """Add local keyword/value pairs when the keyword line lacks a value.

    All plausible peers are retained. Downstream code can flag disagreement;
    proximity alone must not silently decide between two different prices.
    """
    additions = []
    for panel, anchor in lines:
        if not cue.search(anchor.text) or parser(anchor.text):
            continue
        peers = []
        for p, value in lines:
            if value is anchor or not same_surface((panel, anchor), (p, value)):
                continue
            if any(c.search(value.text) for c in other_cues if c is not cue):
                continue
            score = distance(anchor, value)
            if score is None:
                continue
            joined = join(panel, anchor, value)
            if parser(joined.text):
                peers.append((score, joined))
        if peers:
            best = min(score for score, _ in peers)
            additions.extend((panel, line) for score, line in peers if score <= best + 1.5)
    return lines + additions


def price_review_candidates(lines: list[Located]) -> list[dict]:
    """Expose pixel-backed price hypotheses without accepting a declaration.

    Do not feed these supplementary observations into rule arithmetic. The
    currency is a shape hypothesis and the printed amount can still disagree
    with another OCR pass. Original transcriptions and all alternatives remain.
    """
    output, seen = [], set()
    for panel, line in lines:
        hint = getattr(line, "price_hint", None)
        if not hint or not hint.get("requires_review"):
            continue
        for reading in hint.get("readings", []):
            amount_text = str(reading.get("amount_text") or "")
            if not re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", amount_text):
                continue
            amount = float(amount_text)
            if not 0 < amount <= 999999.99:
                continue
            box = reading.get("bbox") or list(line.bbox)
            key = (panel.value, line.frame, getattr(line, "_capture_index", None), tuple(box), amount)
            if key in seen:
                continue
            seen.add(key)
            evidence = {**source(panel, line), "bbox": list(box), "text": reading["text"],
                        "ocr_confidence": reading.get("confidence")}
            output.append({"value": {"value": amount, "currency": "INR", "currency_verified": False},
                           "raw": reading["text"], "sources": [evidence],
                           "ocr_confidence": reading.get("confidence"), "extraction_confidence": None,
                           "status": "needs_review", "method": "pixel_currency_context_candidate",
                           "candidates": [], "ocr_alternatives": list(getattr(line, "alternatives", [])),
                           "currency_evidence": {key: hint.get(key) for key in
                               ("symbol_bbox", "symbol_similarity", "negative_similarity", "score_basis")},
                           "review_reason": "Verify the printed currency and amount against the image; this is not an accepted MRP or tax declaration."})
    return output


def text_block(
    lines: list[Located], anchor: Located, *, stop: re.Pattern | None = None, limit: int = 8,
) -> list[Located]:
    """Walk nearby continuation lines, stopping at another declaration block."""
    block = [anchor]
    current = anchor
    for _ in range(limit):
        candidates = []
        for located in lines:
            if any(located[1] is item[1] for item in block) or not same_surface(anchor, located):
                continue
            score = distance(current[1], located[1])
            if score is None:
                continue
            # A new block heading blocks traversal even if a later number could
            # improve the apparent completeness of the current declaration.
            candidates.append((score, located))
        if not candidates:
            break
        _, next_line = min(candidates, key=lambda item: item[0])
        if stop and stop.search(next_line[1].text):
            break
        block.append(next_line)
        current = next_line
    return block
