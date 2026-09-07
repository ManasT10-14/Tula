"""Local currency-shape hypotheses, explicitly separated from OCR transcription.

The bundled RapidOCR alphabet cannot emit the rupee symbol. A numeral is not a
price by itself: this module requires a nearby actual ink component to match a
rupee reference more closely than competing glyphs. Results always need review;
the hypothesis never overwrites text or supplies an accepted legal declaration.
"""
from __future__ import annotations

import re
from functools import lru_cache

import cv2
import numpy as np

from ._currency_templates import GLYPHS
from .preprocess import Variant, fit_variant, map_polygon

NUMERIC_REGION = re.compile(r"^\s*(?:[₹#?=F]\s*)?\d{1,6}(?:[.,]\d{1,2})?(?:[/-]{1,2}|[17]-)?\s*$")
SLASH_PRICE = re.compile(r"(?<![\w./-])(\d{1,6}(?:\.\d{1,2})?)\s*/\s*-(?!\w)")
PLAIN_AMOUNT = re.compile(r"^\s*(\d{1,6}(?:\.\d{1,2})?)\s*$")


def _normalized_mask(ink):
    yy, xx = np.where(ink > 0)
    if not len(xx):
        return None
    crop = ink[yy.min():yy.max() + 1, xx.min():xx.max() + 1]
    return (cv2.resize(crop, (48, 64), interpolation=cv2.INTER_AREA) > 100).reshape(-1)


@lru_cache(maxsize=1)
def _shape_bank():
    masks, rupees = [], []
    for character, _font, points in GLYPHS:
        reference = np.zeros((180, 180), np.uint8)
        cv2.fillPoly(reference, [np.asarray(points, dtype=np.int32)], 255)
        for angle in (-20, -10, 0, 10, 20):
            for shear in (-.3, 0, .3):
                matrix = cv2.getRotationMatrix2D((90, 90), angle, 1)
                matrix[0, 1] += shear
                masks.append(_normalized_mask(cv2.warpAffine(reference, matrix, (220, 220))))
                rupees.append(character == "₹")
    array = np.asarray(masks, dtype=bool)
    return array, array.sum(axis=1), np.asarray(rupees)


def currency_shape(ink):
    """Return measured mask similarities, never an OCR confidence score."""
    normalized = _normalized_mask(ink)
    if normalized is None:
        return None
    bank, areas, is_rupee = _shape_bank()
    scores = 2 * np.count_nonzero(bank & normalized, axis=1) / (areas + normalized.sum())
    positive, negative = float(scores[is_rupee].max()), float(scores[~is_rupee].max())
    if positive < .75 or positive - negative < .08:
        return None
    return {"symbol_similarity": round(positive, 4), "negative_similarity": round(negative, 4),
            "score_basis": "binary glyph overlap against font references and competing glyphs; uncalibrated"}


def price_regions(image, lines):
    """Yield at most two bounded masks backed by a local rupee-shaped component.

    Only short, otherwise numeric OCR regions qualify. Percentages, dates,
    batches and quantity-unit strings are excluded. Components touching the
    crop boundary are excluded rather than guessing their missing strokes.
    """
    height, width = image.shape[:2]
    eligible = [line for line in lines if NUMERIC_REGION.fullmatch(line.text)
                and line.confidence >= .5 and line.box_height_px >= 25
                and line.width_px > line.box_height_px * .6]
    eligible.sort(key=lambda line: line.height_px or line.box_height_px, reverse=True)
    emitted = 0
    for line in eligible[:3]:
        x0, y0, x1, y1 = line.bbox
        line_height = y1 - y0
        x0 = max(0, x0 - round(line_height * .5))
        # Detectors commonly end at the last tall digit or slash. Include a
        # small real-pixel margin so a trailing short dash is not clipped and
        # then discarded as a boundary-touching component. Never synthesize
        # missing punctuation or derive the amount from a malformed reading.
        x1 = min(width, x1 + max(4, round(line_height * .15)))
        transform = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], dtype=float)
        base = fit_variant(image[y0:y1, x0:x1], max_side=1200, transform=transform)
        gray = cv2.cvtColor(base.image, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        h, w = gray.shape
        best = None
        for ink in (binary, 255 - binary):
            _, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
            kept, symbol = [], None
            for index, (x, y, cw, ch, area) in enumerate(stats[1:], 1):
                if (area < max(40, h * h * .0015) or x == 0 or y == 0
                        or x + cw >= w or y + ch >= h or ch < 12 or cw > h * 1.1):
                    continue
                kept.append(index)
                # Currency glyphs precede the amount and may be smaller. Large
                # numeric glyphs and decorative right-side marks are ineligible.
                if not (.15 < ch / h < .65 and .4 < cw / ch < 1.4 and x < w * .52):
                    continue
                shape = currency_shape(np.where(labels[y:y + ch, x:x + cw] == index, 255, 0).astype(np.uint8))
                if shape and (symbol is None or shape["symbol_similarity"] > symbol[0]["symbol_similarity"]):
                    symbol = shape, (int(x), int(y), int(x + cw), int(y + ch))
            if symbol is None or len(kept) < 2:
                continue
            shape, box = symbol
            if best is not None and best[0]["symbol_similarity"] >= shape["symbol_similarity"]:
                continue
            cleaned = np.where(np.isin(labels, kept), 0, 255).astype(np.uint8)
            best = shape, box, cleaned
        if best is None:
            continue
        shape, box, cleaned = best
        polygon = map_polygon([(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])],
                              base.to_original, width, height)
        hint = {**shape, "currency": "INR", "currency_verified": False, "requires_review": True,
                "method": "currency_shape_with_numeric_region",
                "symbol_bbox": [round(min(p[0] for p in polygon)), round(min(p[1] for p in polygon)),
                                round(max(p[0] for p in polygon)), round(max(p[1] for p in polygon))],
                "source_text": line.text,
                "reason": "A nearby ink shape resembles a rupee symbol; verify the original price and currency."}
        amount = PLAIN_AMOUNT.fullmatch(line.text)
        hint["amount_text"] = amount[1] if amount else None
        variant = fit_variant(cv2.cvtColor(cleaned, cv2.COLOR_GRAY2BGR),
                              name=f"price_context_{emitted + 1}", upscale=3, max_side=1600,
                              transform=base.to_original)
        yield line, Variant(variant.name, variant.image, variant.to_original), hint
        emitted += 1
        if emitted == 2:
            return


def attach_price_hint(line, hint):
    """Record a hypothesis without replacing the model's literal transcription."""
    line.price_hint = dict(hint)
    amount = SLASH_PRICE.search(line.text)
    if amount:
        line.price_hint.update(amount_text=amount[1], source_text=line.text,
                               method="currency_shape_and_recognized_slash_notation")
    return line
