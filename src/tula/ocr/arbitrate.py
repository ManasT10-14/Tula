"""Compare recognition passes by evidence location without a confidence vote.

Preprocessing variants share a model, so agreement is not independent evidence
and must never manufacture a higher confidence score.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import replace
from difflib import SequenceMatcher
from itertools import product

from .base import OcrLine

_MONTH_WORDS = frozenset(word.casefold() for words in (calendar.month_abbr, calendar.month_name)
                         for word in words if word)
# This grammar detects uncertainty only. It never changes a transcription or
# supplies a normalized date. All twelve named months are treated alike.
_MONTH_TOKEN = re.compile(r"(?<!\w)([a-z0-9]{3,9})\s*([/.-])\s*(\d{4}|\d{2})(?!\w)", re.IGNORECASE)
_CONFUSABLE = {"0": "o", "1": "il", "5": "s", "8": "b"}
_CODE_CUE = re.compile(
    r"\b(?:sku|model|part|item|ref(?:erence)?|serial|catalog(?:ue)?|batch|lot|"
    r"(?:coupon|promo(?:tion)?|offer)\s*code|code)\b", re.IGNORECASE)
_DATE_CUE = re.compile(
    r"\b(?:mfg|mfd|pkd|exp(?:iry|ires|iration)?|manufactur\w*|pack(?:ed|ing)|"
    r"best\s*(?:before|by)|use\s*(?:before|by)|date)\b", re.IGNORECASE)


def _code_context(text, position):
    """An explicit code label stays in scope until a printed date cue occurs."""
    codes = list(_CODE_CUE.finditer(text, 0, position))
    dates = list(_DATE_CUE.finditer(text, 0, position))
    return bool(codes and (not dates or codes[-1].start() > dates[-1].start()))


def _month_kind(token):
    value = token.casefold()
    if value in _MONTH_WORDS:
        return "literal_month"
    # Bound ambiguity detection to at most two potentially confused glyphs.
    # Ordinary alphanumeric SKU fragments are not automatically month tokens.
    digits = [char for char in value if char.isdigit()]
    if not 1 <= len(digits) <= 2 or any(char not in _CONFUSABLE for char in digits):
        return None
    choices = [_CONFUSABLE.get(char, char) for char in value]
    if any("".join(chars) in _MONTH_WORDS for chars in product(*choices)):
        return "uncertain_month_like_token"
    return None


def _date_tokens(text):
    output = []
    for match in _MONTH_TOKEN.finditer(text):
        kind = _month_kind(match[1])
        if kind and not _code_context(text, match.start()):
            output.append({"raw": match[0], "start": match.start(), "end": match.end(), "kind": kind})
    return output


def date_token_uncertainty(text):
    """Return observed suspicious tokens, without invented letter/date alternatives."""
    return [token for token in _date_tokens(text) if token["kind"] == "uncertain_month_like_token"]


def _same_extent_date_disagreement(left, right):
    # A year-only crop or a crop containing one of two real dates must remain
    # usable supporting evidence, not an invented contradiction. Compare equal
    # token counts on substantially overlapping full recognition extents.
    if overlap(left.bbox, right.bbox)[0] < .65:
        return False
    a, b = _date_tokens(left.text), _date_tokens(right.text)
    if not a or len(a) != len(b):
        return False
    literal = lambda token: re.sub(r"\s", "", token["raw"]).casefold()
    return any(literal(x) != literal(y) for x, y in zip(a, b))


def canonical(text):
    return re.sub(r"[^\w]", "", text.casefold())


def overlap(a, b):
    x0, y0, x1, y1 = a
    u0, v0, u1, v1 = b
    inter = max(0, min(x1, u1) - max(x0, u0)) * max(0, min(y1, v1) - max(y0, v0))
    aa, bb = max(1, (x1 - x0) * (y1 - y0)), max(1, (u1 - u0) * (v1 - v0))
    return inter / (aa + bb - inter), inter / min(aa, bb)


def candidate_dict(line):
    return {"text": line.text, "bbox": list(line.bbox), "polygon": line.polygon,
            "confidence": round(line.confidence, 5), "variant": line.variants[0] if line.variants else "original",
            "angle_degrees": round(line.angle_degrees, 2)}


def arbitrate(candidates: list[OcrLine]) -> tuple[list[OcrLine], list[dict]]:
    groups: list[list[OcrLine]] = []
    for line in candidates:
        if not line.text.strip():
            continue
        for group in groups:
            # Never collapse two independent boxes from the same pass. They
            # can be separate declarations, including genuinely different MRPs.
            if any(set(line.variants) & set(other.variants) for other in group):
                continue
            ref = group[0]
            iou, containment = overlap(ref.bbox, line.bbox)
            similarity = SequenceMatcher(None, canonical(ref.text), canonical(line.text)).ratio()
            if iou >= 0.38 or (containment > 0.8 and similarity > 0.72):
                group.append(line)
                break
        else:
            groups.append([line])
    selected, conflicts = [], []
    for group in groups:
        best = max(group, key=lambda l: (l.confidence, l.variants == ["original"]))
        # A crop may contain only the number and receive a marginally higher
        # score than the complete declaration. Keep useful keyword context
        # when its score is comparable; candidate scores are still unchanged.
        comparable = [l for l in group if l.confidence >= best.confidence - 0.05]
        best = max(comparable, key=lambda l: (len(canonical(l.text)), l.confidence))
        strong = [l for l in group if l.confidence >= 0.55]
        disputed = []
        for candidate in strong:
            if candidate is best:
                continue
            left, right = canonical(best.text), canonical(candidate.text)
            numbers_a, numbers_b = re.findall(r"\d+(?:[.,/]\d+)*", best.text), re.findall(r"\d+(?:[.,/]\d+)*", candidate.text)
            # Crop recognizers sometimes drop a prefix/suffix without reading
            # the value differently. Only genuine differing numeric tokens or
            # substantially different text on the same extent require review.
            numeric_conflict = bool(numbers_a and numbers_b and numbers_a != numbers_b
                                    and not (set(numbers_a) <= set(numbers_b) or set(numbers_b) <= set(numbers_a)))
            text_conflict = bool(left and right and left not in right and right not in left
                                 and SequenceMatcher(None, left, right).ratio() < 0.6)
            if numeric_conflict or text_conflict or _same_extent_date_disagreement(best, candidate):
                disputed.append(candidate)
        uncertain_tokens = date_token_uncertainty(best.text)
        alternatives = [candidate_dict(l) for l in group]
        variants = list(dict.fromkeys(v for l in group for v in l.variants))
        chosen = replace(best, variants=variants, alternatives=alternatives,
                         review_required=bool(disputed or uncertain_tokens),
                         confidence=min(best.confidence, 0.54) if disputed or uncertain_tokens else best.confidence)
        if disputed or uncertain_tokens:
            conflicts.append({"bbox": list(best.bbox), "selected_text": best.text,
                              "candidates": alternatives,
                              "kind": "recognition_disagreement" if disputed else "date_token_uncertainty",
                              "date_token_diagnostics": uncertain_tokens,
                              "reason": ("Recognition passes disagree on the same printed region." if disputed else
                                  "A date-like token mixes letters and digits in its month-like component. Its literal reading is uncertain; no date meaning or corrected spelling is inferred."),
                              "action": "Compare the original image, correct the field with a reason, or capture a closer image."})
        selected.append(chosen)
    # A full-line detector and a crop detector can segment the same printing
    # differently. Suppress already represented fragments only when they carry
    # the same literal content; keep conflicting fragments for review.
    deduplicated = []
    for line in selected:
        duplicate = any(
            other is not line and not line.review_required
            and len(canonical(other.text)) > len(canonical(line.text))
            and canonical(line.text) in canonical(other.text)
            and overlap(line.bbox, other.bbox)[1] > 0.60
            for other in selected
        )
        if not duplicate:
            deduplicated.append(line)
    return deduplicated, conflicts
