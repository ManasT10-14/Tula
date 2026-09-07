"""Local semantic context for label identity and package quantities.

These are conservative layout heuristics, never a product/brand lookup. All
positions remain on the recorded image and OCR text is not spell-corrected.
"""
from __future__ import annotations

import copy
import math
import re

from . import layout

NUTRITION = re.compile(
    r"\b(?:sodium|protein|carbohydrates?|sugars?|fat|fibre|fiber|energy|calories?|"
    r"cholesterol|potassium|calcium|iron|vitamins?|nutrition(?:al)?|servings?|"
    r"per\s*(?:serve|portion|100)|daily\s*(?:value|allowance)|RDA)\b", re.IGNORECASE)
INGREDIENT = re.compile(r"\b(?:ingredients?|contains?|may\s*contain|allergens?)\b|\btains\s*:", re.IGNORECASE)
FLAVOUR = re.compile(
    r"\b(?:with|taste|tasty|yummy|flavou?r(?:ed)?|favourite|favorite|"
    r"made\s*with|goodness\s*of|rich\s*in|enriched\s*with)\b", re.IGNORECASE)
INSTRUCTION = re.compile(
    r"\b(?:prepar\w*|cook\w*|store|storage|conditions|dispose|litter|waste|"
    r"hygienic|dry|recycl\w*|customer|www|email|minutes?|seconds?|"
    r"approx|amounts?|total)\b|\b\w+\.com\b", re.IGNORECASE)
PROMOTION = re.compile(r"\b(?:new|improved|extra|free|offer|off|your|quality|"
                       r"original|bake[ds]?|fried|not|hot)\b|%", re.IGNORECASE)
BRAND_CUE = re.compile(r"^\s*(?:brand(?:\s*name)?|trade\s*name)\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE)


def readable_identity_support(located, minimum_confidence):
    """Readable commodity context is separate from prominence or name ranking."""
    line = located[1]
    return (line.confidence >= minimum_confidence and not getattr(line, "review_required", False)
            and not getattr(line, "_identity_degraded", False)
            and "\ufffd" not in line.text
            and not line.text.rstrip().endswith(("*", "†", "‡")))


def identity_support(candidate, commodity):
    """Positive evidence must belong to the candidate's physical capture.

    The caller supplies a commodity already checked for legibility, semantic
    context and uncertainty. These sources support identity interpretation;
    they do not replace the literal brand's own transcription or OCR score.
    """
    if BRAND_CUE.search(candidate[1].text):
        return {"kind": "explicit_brand_cue", "sources": layout.sources(*candidate)}
    if commodity and layout.same_surface(candidate, commodity[1]):
        return {"kind": "same_image_commodity", "commodity": commodity[0],
                "sources": layout.sources(*commodity[1])}
    return None


def angle(line):
    value = float(getattr(line, "angle_degrees", 0) or 0)
    # Some recognisers leave their angle in the rotated crop's coordinates.
    # A tall narrow original-image region provides the remaining axis clue.
    if abs(value) < 20 and line.box_height_px > line.width_px * 2:
        value = 90.0
    return value


def extents(line, theta):
    radians = math.radians(theta)
    u, v = (math.cos(radians), math.sin(radians)), (-math.sin(radians), math.cos(radians))
    x0, y0, x1, y1 = line.bbox
    points = getattr(line, "polygon", None) or [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return tuple((min(x * axis[0] + y * axis[1] for x, y in points),
                  max(x * axis[0] + y * axis[1] for x, y in points)) for axis in (u, v))


def height(line):
    # A rotated detector's axis-aligned box height is not its glyph height.
    box_height = max(4, min(line.width_px, line.box_height_px))
    return min(line.height_px or box_height, box_height)


def neighbor(first, second, *, max_gap=2.0, before=True):
    """Adjacent text on one surface, in the first region's reading axes."""
    if first[1] is second[1] or not layout.same_surface(first, second):
        return False
    a, b = first[1], second[1]
    ax, ay = extents(a, angle(a))
    bx, by = extents(b, angle(a))
    h = max(4, min(height(a), height(b)))
    cx, cy = (sum(ax) / 2, sum(ay) / 2)
    dx, dy = (sum(bx) / 2 - cx, sum(by) / 2 - cy)
    gap_x = max(0, bx[0] - ax[1], ax[0] - bx[1])
    gap_y = max(0, by[0] - ay[1], ay[0] - by[1])
    side_by_side = bx[0] >= ax[1] - .5 * h or (not before and ax[0] >= bx[1] - .5 * h)
    horizontal = side_by_side and abs(dy) <= 1.1 * h and gap_x <= max_gap * h and (not before or dx >= 0)
    overlap = min(ax[1], bx[1]) - max(ax[0], bx[0])
    aligned = overlap >= .3 * min(ax[1] - ax[0], bx[1] - bx[0])
    vertical = aligned and gap_y <= max_gap * h and (not before or dy >= .25 * h)
    return horizontal or vertical


def adjacent_cues(lines, located, pattern, *, both_sides=False):
    return [other for other in lines if pattern.search(other[1].text)
            and neighbor(other, located, before=not both_sides)]


def generic_context_blocked(lines, located, disqualifiers, *, text=None):
    text = located[1].text if text is None else text
    if BRAND_CUE.search(text):
        return True
    if disqualifiers.search(text) or FLAVOUR.search(text) or INGREDIENT.search(text) or NUTRITION.search(text):
        return True
    # An ingredient list can survive a cropped/missed heading. A comma-separated
    # list is not an isolated commodity name (e.g. TAINS:WHEAT,MILK).
    if re.search(r"[,;]", text):
        return True
    if adjacent_cues(lines, located, disqualifiers) or adjacent_cues(lines, located, INGREDIENT):
        return True
    if adjacent_cues(lines, located, FLAVOUR):
        return True
    # "Chocolate" followed by "flavour" also qualifies the preceding token.
    return bool(adjacent_cues(lines, located, re.compile(r"\bflavou?r\b", re.IGNORECASE), both_sides=True))


def quantity_allowed(lines, located, cue):
    """Exclude nutrition cells before a net cue can borrow their numbers."""
    text = located[1].text
    if cue.search(text):
        return True
    if NUTRITION.search(text) or INGREDIENT.search(text) or INSTRUCTION.search(text):
        return False
    return not (adjacent_cues(lines, located, NUTRITION) or adjacent_cues(lines, located, INGREDIENT))


def brand_text_allowed(lines, located, *, generic_patterns):
    text = located[1].text.strip()
    if any(pattern.search(text) for pattern in (NUTRITION, INGREDIENT, INSTRUCTION, PROMOTION, FLAVOUR)):
        return False
    if re.search(r"[,;:@]|\d", text) or len(text.split()) > 4:
        return False
    if adjacent_cues(lines, located, FLAVOUR, both_sides=True) or adjacent_cues(lines, located, INGREDIENT):
        return False
    if any(pattern.search(text) for pattern in generic_patterns):
        return False
    # Narrow vertical side text and folded instructions can have misleadingly
    # large glyph heights. Require plausible width along their reading axis.
    return not (abs(float(getattr(located[1], "angle_degrees", 0))) < 20
                and located[1].box_height_px > located[1].width_px * 2)


def join_brand(candidates, selected):
    """Join comparable adjacent identity fragments, retaining each source box."""
    primary = selected[1]
    parts = [selected]
    for candidate in candidates:
        peer = candidate[1]
        if peer is primary or peer.text.casefold() == primary.text.casefold():
            continue
        ratio = min(height(peer), height(primary)) / max(height(peer), height(primary))
        delta = abs((angle(primary) - angle(peer) + 90) % 180 - 90)
        if ratio < .65 or delta > 18 or not neighbor(selected, candidate, max_gap=1.0, before=False):
            continue
        parts.append(candidate)
    if len(parts) == 1:
        return selected
    # Bound joining to a short logo/name, never concatenate a paragraph.
    if len(parts) > 3 or sum(len(part[1].text.split()) for part in parts) > 5:
        return selected
    theta = angle(primary)
    bounds = {id(part[1]): extents(part[1], theta) for part in parts}
    centers = [sum(bounds[id(part[1])][1]) / 2 for part in parts]
    same_baseline = max(centers) - min(centers) <= .75 * min(height(part[1]) for part in parts)
    axis = 0 if same_baseline else 1
    parts.sort(key=lambda part: sum(bounds[id(part[1])][axis]))
    joined = copy.copy(primary)
    joined.text = " ".join(part[1].text.strip() for part in parts)
    joined.confidence = min(part[1].confidence for part in parts)
    joined._source_spans = [source for part in parts for source in layout.sources(*part)]
    joined._extraction_method = "brand_layout_heuristic"
    joined.review_required = any(getattr(part[1], "review_required", False) for part in parts)
    return selected[0], joined
