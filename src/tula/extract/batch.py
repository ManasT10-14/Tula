"""Conservative batch/lot cue parsing without rewriting the OCR transcript.

A joined cue needs an uppercase, digit-bearing code shape. Alphabetic words,
dates and prices are insufficient evidence of a batch identifier. Delimited
codes may use either case; no known brand, filename or expected code is used.
"""
from __future__ import annotations

import re

# Keep the officer-correction cue contract distinct from machine inference.
STANDARD_CUE = re.compile(r"\b(?:batch|lot)(?:\s*(?:no\.?|number|code))?\b|बैच\s*(?:संख्या|नं\.?)?", re.IGNORECASE)
_JOINED = r"\b(?:batch|lot)(?=(?-i:[A-Z]{0,8}[._/-]?\d[A-Z0-9._/-]{0,31})(?![A-Za-z0-9._/-]))"
# Indian packs very often abbreviate the batch cue to a single letter -- "B:
# 103A" stamped beside the date code, which is why a real photograph in the
# evaluation set carried a batch nothing could extract. One letter is weak
# evidence, so the short form is admitted only with a delimiter after it, only
# for machine inference, and only for a code carrying both a letter and a
# digit. The officer-correction contract in STANDARD_CUE is left alone.
_SHORT = r"(?<![A-Za-z])B(?:\.?\s*no\.?)?(?=\s*[:#]\s*\S)"
EXTRACTION_CUE = re.compile(
    r"(?:" + STANDARD_CUE.pattern + r")|(?:" + _JOINED + r")|(?:" + _SHORT + r")",
    re.IGNORECASE,
)
_SPELT_CUE = re.compile(r"(?:batch|lot|बैच)", re.IGNORECASE)
# A single letter is not a cue when a preceding word owns it.
_SHORT_DISQUALIFIER = re.compile(
    r"(?:vitamin|vit\.?|group|grade|class|type|plan|hall|gate|विटामिन)\s*$", re.IGNORECASE
)
_TOKEN = re.compile(r"([A-Z0-9][A-Z0-9./_-]{0,39})(?![A-Z0-9./_-])", re.IGNORECASE)
_QUALIFIER = re.compile(r"^(?:number|code|no\.?)(?=\s|[:#./-]|\d)\s*", re.IGNORECASE)
_OTHER_FIELD = re.compile(r"^(?:m\.?r\.?p\.?|rs\.?|inr|exp(?:iry|iration)?|mfg|mfd|pkd|net|useby|bestbefore)(?=\d|\W|$)", re.IGNORECASE)
_FUSED_FIELD = re.compile(r"(?<=\d)(?:MRP|EXP|MFG|MFD|PKD|INR)(?=\d)", re.IGNORECASE)
_NUMERIC_DATE = re.compile(r"\d{1,4}[/.-]\d{1,4}(?:[/.-]\d{1,4})?")
_NAMED_DATE = re.compile(r"(?:\d{1,2})?(?:JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:T(?:EMBER)?)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)[./-]?\d{2,4}", re.IGNORECASE)
_QUANTITY = re.compile(r"\d+(?:[.,]\d+)?(?:kg|mg|g|ml|cl|l|oz|lbs?)", re.IGNORECASE)
_PROMOTIONAL_PREFIX = re.compile(r"\b(?:small|fresh|artisan|handmade|handcrafted|hand.crafted|limited|every|each|per)\s*[-:]?\s*$", re.IGNORECASE)


def joined_cue(text: str) -> bool:
    cue = EXTRACTION_CUE.search(text)
    return bool(cue and cue.end() < len(text) and text[cue.end()].isalnum())


def parse_batch(text: str) -> str | None:
    cue = EXTRACTION_CUE.search(text)
    if not cue or _PROMOTIONAL_PREFIX.search(text[:cue.start()]):
        return None
    # The short form is whatever matched that is not the spelt-out word. It
    # cannot be re-matched in isolation, because its pattern ends in a
    # lookahead that only exists in the surrounding line.
    short = not _SPELT_CUE.match(cue.group(0))
    if short and _SHORT_DISQUALIFIER.search(text[:cue.start()]):
        return None
    tail = text[cue.end():]
    joined = bool(tail and tail[0].isalnum())
    # With no boundary, NO123 may be either a qualifier or the code itself.
    # Do not silently discard or invent that prefix.
    if joined and re.match(r"(?:NO|NUMBER|CODE)\d", tail, re.IGNORECASE):
        return None
    if not joined:
        tail = tail.lstrip(" \t\r\n:#.-")
        tail = _QUALIFIER.sub("", tail, count=1).lstrip(" \t\r\n:#.-")
    match = _TOKEN.match(tail)
    if not match:
        return None
    value = match[1].rstrip(".")
    rest = tail[match.end():]
    if (not value or not value[-1].isalnum() or not any(char.isdigit() for char in value)
            or _OTHER_FIELD.match(value) or _FUSED_FIELD.search(value)
            or _NUMERIC_DATE.fullmatch(value) or _NAMED_DATE.fullmatch(value)
            or _QUANTITY.fullmatch(value)):
        return None
    if rest and not (rest[0].isspace() or rest[0] in ",;:)]}"):
        return None  # Never accept the prefix of an unsupported code/amount.
    # One letter of cue buys less trust than the word "batch": a bare number
    # after it is a quantity, a vitamin or a shelf position far more often
    # than a batch identifier, so the short form demands a mixed code.
    if short and not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return None
    # A bare amount beside a unit/currency/percentage is not a batch identifier.
    if value.isdigit() and re.match(r"\s*(?:[%₹$€£]|INR\b|Rs\b|kg\b|mg\b|g\b|ml\b|l\b|/-)", rest, re.IGNORECASE):
        return None
    return value
