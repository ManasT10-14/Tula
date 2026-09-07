"""Grammars that turn printed text into comparable values.

Rules are only ever allowed to reason about the output of this module. That is
what lets "Rs.100/-", "MRP 100.00", "M.R.P ₹100" and "₹ 100 /-" collapse into
one number, and it keeps regex out of the rule pack where a legal officer would
have to read it.

The unit-symbol table is the interesting part: Legal Metrology prescribes exact
symbols, and "500 gms" is a real, citable non-conformity that presence-detection
can never see.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from ..domain.enums import Script

# ---------------------------------------------------------------------------
# Unit symbols
# ---------------------------------------------------------------------------

# Prescribed symbols. Case matters: "Kg" is not the symbol for kilogram.
CANONICAL_UNITS: dict[str, tuple[str, float, str]] = {
    # symbol: (kind, factor to base unit, base unit)
    "mg": ("mass", 0.001, "g"),
    "g": ("mass", 1.0, "g"),
    "kg": ("mass", 1000.0, "g"),
    "ml": ("volume", 1.0, "ml"),
    "l": ("volume", 1000.0, "ml"),
    "mm": ("length", 0.1, "cm"),
    "cm": ("length", 1.0, "cm"),
    "m": ("length", 100.0, "cm"),
    "N": ("count", 1.0, "N"),
}

# "mL" is SI-legal and in wide use; treating it as a violation would be crying
# wolf, so it is accepted here even though the schedule prints "ml".
TOLERATED = {"mL": "ml", "L": "l"}

# Non-conforming spellings actually seen on Indian retail packaging, mapped to
# what the packer meant. Presence of a key here is the violation.
NON_CANONICAL: dict[str, str] = {
    "gm": "g", "gms": "g", "gm.": "g", "gms.": "g", "GM": "g", "GMS": "g",
    "Gm": "g", "Gms": "g", "grm": "g", "grms": "g", "gram": "g", "grams": "g",
    "G": "g", "Grams": "g", "Gram": "g",
    "Kg": "kg", "KG": "kg", "kgs": "kg", "Kgs": "kg", "KGS": "kg", "kg.": "kg",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "Kilo": "kg",
    "ML": "ml", "Ml": "ml", "mls": "ml", "ml.": "ml", "millilitre": "ml",
    "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "L": "l", "ltr": "l", "Ltr": "l", "LTR": "l", "ltrs": "l", "Ltrs": "l",
    "litre": "l", "litres": "l", "liter": "l", "liters": "l", "lt": "l",
    "MG": "mg", "Mg": "mg", "mgs": "mg",
    "CM": "cm", "Cm": "cm", "cms": "cm",
    "MM": "mm", "Mm": "mm", "mms": "mm",
    "mtr": "m", "MTR": "m", "metre": "m", "metres": "m", "meter": "m",
    "pc": "N", "pcs": "N", "Pcs": "N", "PCS": "N", "nos": "N", "Nos": "N",
    "NOS": "N", "no.": "N", "piece": "N", "pieces": "N", "units": "N",
    "packet": "N", "pkt": "N", "pack": "N", "unit": "N",
}

_UNIT_ALTERNATION = "|".join(
    sorted(
        (re.escape(u) for u in list(CANONICAL_UNITS) + list(TOLERATED) + list(NON_CANONICAL)),
        key=len,
        reverse=True,
    )
)


def canonical_unit(raw: str) -> dict[str, Any]:
    """Resolve a printed unit symbol against the prescribed set."""
    token = raw.strip()
    if token in CANONICAL_UNITS:
        kind, factor, base = CANONICAL_UNITS[token]
        return {"unit_raw": token, "unit": token, "is_canonical": True,
                "kind": kind, "factor": factor, "unit_base": base}
    if token in TOLERATED:
        canon = TOLERATED[token]
        kind, factor, base = CANONICAL_UNITS[canon]
        return {"unit_raw": token, "unit": canon, "is_canonical": True,
                "kind": kind, "factor": factor, "unit_base": base}
    if token in NON_CANONICAL:
        canon = NON_CANONICAL[token]
        kind, factor, base = CANONICAL_UNITS[canon]
        return {"unit_raw": token, "unit": canon, "is_canonical": False,
                "kind": kind, "factor": factor, "unit_base": base,
                "expected": canon}
    lowered = token.lower()
    if lowered in CANONICAL_UNITS:
        canon = lowered
        kind, factor, base = CANONICAL_UNITS[canon]
        return {"unit_raw": token, "unit": canon, "is_canonical": token == canon,
                "kind": kind, "factor": factor, "unit_base": base,
                "expected": canon}
    return {"unit_raw": token, "unit": None, "is_canonical": False, "kind": None,
            "factor": None, "unit_base": None}


# ---------------------------------------------------------------------------
# Numbers and quantities
# ---------------------------------------------------------------------------

_NUM = r"\d+(?:[, ]\d{2,3})*(?:\.\d+)?"

_QTY_RE = re.compile(rf"(?<![\d.\-])({_NUM})\s*({_UNIT_ALTERNATION})(?![A-Za-z])")
_QTY_HINT = re.compile(
    r"net\s*(?:qty|quantity|wt\.?|weight|vol\.?|volume|content|contents)", re.IGNORECASE
)
_EXTRA_FREE_RE = re.compile(r"\+\s*(\d+(?:\.\d+)?)\s*(\w+)\s*(?:extra|free)", re.IGNORECASE)


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", "").replace(" ", "").strip())
    except ValueError:
        return None


def parse_net_quantity(text: str) -> dict[str, Any] | None:
    """Read a net-quantity declaration.

    Prefers a quantity that sits near a "Net Qty"-style cue, and falls back to
    the first standalone quantity on the label. The cue matters: a nutrition
    panel is full of gram values that are not the net quantity.
    """

    # Nutrition, serving sizes and unit-price denominators are not net contents.
    candidate_text = "\n".join(line for line in text.splitlines() if _QTY_HINT.search(line) or not re.search(r"\b(?:protein|fat|sugars?|carbohydrate|sodium|fibre|fiber|serving|nutrition|per\b|unit\s*(?:sale\s*)?price)\b|(?:₹|Rs\.?|INR).*[/]", line, re.IGNORECASE))
    matches = list(_QTY_RE.finditer(candidate_text))
    if not matches:
        return None

    chosen = None
    for cue in _QTY_HINT.finditer(candidate_text):
        after = [m for m in matches if m.start() >= cue.end()]
        if after:
            chosen = min(after, key=lambda m: m.start() - cue.end())
            break
    if chosen is None:
        chosen = matches[0]

    value = _to_float(chosen.group(1))
    if value is None or not math.isfinite(value) or value <= 0:
        return None

    unit = canonical_unit(chosen.group(2))
    out: dict[str, Any] = {"value": value, **unit, "raw": chosen.group(0)}
    if unit["factor"] is not None:
        out["value_base"] = value * unit["factor"]
    else:
        out["value_base"] = None

    extra = _EXTRA_FREE_RE.search(text)
    if extra:
        # "90 g + 10 g free" carries its own declaration rules; flag it for the
        # officer rather than silently folding it into the net quantity.
        out["has_extra_free"] = True
        out["extra_free_raw"] = extra.group(0)
    return out


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

_MRP_CUE = r"(?:m\.?\s*r\.?\s*p\.?|maximum\s+retail\s+price|max\.?\s*retail\s*price)"
_CURRENCY = r"(?:₹|rs\.?|inr)"
# Two shapes, because both are common on Indian packs: the currency ahead of
# the figure ("MRP Rs. 45.00") and behind it ("MRP: 1,250.00 Rs"). The optional
# separator after the cue covers "MRP:" and "M.R.P -".
_PRICE_RE = re.compile(
    rf"(?:{_MRP_CUE})?\s*[:\-]?\s*(?:{_CURRENCY})\s*({_NUM})"
    rf"|(?:{_MRP_CUE})\s*[:\-]?\s*({_NUM})\s*(?:{_CURRENCY})?",
    re.IGNORECASE,
)
_UNIT_PRICE_RE = re.compile(
    rf"(?:unit\s*(?:sale\s*)?price\s*)?(?:{_CURRENCY})\s*({_NUM})\s*"
    rf"(?:/|per\b)\s*({_NUM})?\s*({_UNIT_ALTERNATION})(?![A-Za-z])",
    re.IGNORECASE,
)
_TAX_PHRASES = ("inclusive of all taxes", "incl of all taxes", "incl. of all taxes")


def parse_unit_price(text: str) -> dict[str, Any] | None:
    """Read a unit sale price such as "₹22.50 per 100 g" or "Rs 225/kg"."""
    match = _UNIT_PRICE_RE.search(text)
    if not match:
        return None
    value = _to_float(match.group(1))
    if value is None:
        return None
    per_value = _to_float(match.group(2)) if match.group(2) else 1.0
    unit = canonical_unit(match.group(3))
    if unit["factor"] is None or per_value is None or per_value <= 0 or value < 0:
        return None
    return {
        "value": value,
        "per_value": per_value,
        "per_unit": unit["unit"],
        # expressed in the same base unit as net quantity, so the arithmetic
        # audit is a straight division
        "per_base": per_value * unit["factor"],
        "unit_base": unit["unit_base"],
        "raw": match.group(0),
    }


def parse_price(text: str, *, exclude: str | None = None) -> dict[str, Any] | None:
    """Read the retail sale price, and count how many distinct ones appear.

    The count is what makes dual pricing and an over-applied revised price
    detectable at all -- a single-value parser would just report the last one
    it saw.
    """

    haystack = text
    if exclude:
        haystack = haystack.replace(exclude, " ")
    haystack = _UNIT_PRICE_RE.sub(" ", haystack)

    values: list[float] = []
    raws: list[str] = []
    for match in _PRICE_RE.finditer(haystack):
        raw = match.group(1) or match.group(2)
        value = _to_float(raw) if raw else None
        if value is None or value <= 0:
            continue
        values.append(value)
        raws.append(match.group(0).strip())

    if not values:
        return None

    distinct = sorted({round(v, 2) for v in values})
    lowered = text.lower()
    has_tax = any(p in lowered for p in _TAX_PHRASES)
    return {
        "value": distinct[-1] if len(distinct) == 1 else max(distinct),
        "currency": "INR",
        "distinct_values": distinct,
        "count": len(distinct),
        "raw_prices": raws,
        "has_tax_clause": has_tax,
    }


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_CUE = re.compile(
    r"(?:mfg|mfd|manufactured|month\s+and\s+year|date\s+of\s+(?:pack\w*|manufactur\w*)"
    r"|packed\s+(?:on|in)|pkd)", re.IGNORECASE
)
_NUM_DATE = re.compile(r"(?<![\d/.-])(0?[1-9]|1[0-2])\s*[/\-.]\s*(20\d{2}|\d{2})(?!\d)")
_ALPHA_DATE = re.compile(
    r"(" + "|".join(_MONTHS) + r")[a-z]*\.?\s*[,/\-]?\s*(20\d{2}|\d{2})", re.IGNORECASE
)


def parse_date(text: str) -> dict[str, Any] | None:
    """Read the month and year of manufacture, packing or import."""
    window = text
    cue = _DATE_CUE.search(text)
    if cue:
        window = text[cue.end() : cue.end() + 60]
    window = re.split(r"\b(?:best\s*before|use\s*by|exp(?:iry|ires)?)\b", window, flags=re.IGNORECASE)[0]

    iso = re.search(r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", window)
    full = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|\d{2})(?!\d)", window)
    if iso or full:
        match = iso or full
        y, m, d = map(int, iso.groups()) if iso else (int(full[3]), int(full[2]), int(full[1]))
        y += 2000 if y < 100 else 0
        # Without an explicitly printed format, 08/09/2026 can mean either
        # August 9 or September 8. Supplementary intelligence preserves both;
        # legal effective-date selection must not silently choose one.
        if (full and 1 <= int(full[1]) <= 12 and 1 <= int(full[2]) <= 12 and full[1] != full[2]
                and not re.search(r"\bdd\s*[/.-]\s*mm\s*[/.-]\s*y{2,4}\b", text, re.IGNORECASE)):
            return None
        try:
            date(y, m, d)
        except ValueError:
            return None
        return {"month": m, "year": y, "iso": f"{y:04d}-{m:02d}", "raw": match[0]}

    month = year = None
    match = _ALPHA_DATE.search(window)
    if match:
        month = _MONTHS[match.group(1)[:3].lower()]
        year = int(match.group(2))
    else:
        match = _NUM_DATE.search(window)
        if match:
            month = int(match.group(1))
            year = int(match.group(2))
    if year is None:
        return None
    if year < 100:
        year += 2000
    return {"month": month, "year": year, "iso": f"{year:04d}-{month:02d}" if month else None,
            "raw": match.group(0) if match else None}


# ---------------------------------------------------------------------------
# Contact details
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+91[\s\-]?)?(?:1800[\s\-]?\d{3}[\s\-]?\d{3,4}|[6-9]\d{9})")
_PIN_RE = re.compile(r"\b([1-9]\d{5})\b")
_ADDRESS_CUE = re.compile(
    r"(?:road|rd\.|street|st\.|marg|nagar|colony|sector|plot|survey|village|taluk"
    r"|district|dist\.|po\b|p\.o\.|industrial|estate|phase|block|floor|building)", re.IGNORECASE
)


def parse_contact(text: str) -> dict[str, Any]:
    """Extract contactability signals from a manufacturer or care block."""
    email = _EMAIL_RE.search(text)
    phone = _PHONE_RE.search(text)
    pin = _PIN_RE.search(text)
    # A printed office name or named company/person must be in this same
    # contact block. E-mail domains and telephone numbers are not names.
    name = re.search(
        r"\b(?:consumer\s+(?:care|services?|complaints?)|customer\s+(?:care|services?)"
        r"|(?:complaints?|grievance)\s+(?:officer|office|cell)|[A-Za-z][A-Za-z &.'-]{1,60}"
        r"\s+(?:limited|ltd\.?|pvt\.?|llp)|(?:contact\s+person|name)\s*:\s*[A-Za-z][A-Za-z .'-]{2,50})\b",
        text, re.IGNORECASE,
    )
    return {
        "name": name.group(0).strip() if name else None,
        "has_name": name is not None,
        "email": email.group(0) if email else None,
        "phone": re.sub(r"[\s\-]", "", phone.group(0)) if phone else None,
        "pin": pin.group(1) if pin else None,
        "has_email": email is not None,
        "has_phone": phone is not None,
        "has_pin": pin is not None,
        # an address needs more than a PIN: a locality cue or a PIN plus enough
        # text to be a real postal address
        "has_address": bool(pin or _ADDRESS_CUE.search(text)),
    }


# ---------------------------------------------------------------------------
# Script identification -- Rule 9(3)
# ---------------------------------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
# A recogniser with no Devanagari in its dictionary still *detects* the text
# box and emits placeholders for it. Reading that as "Hindi is absent" would
# turn a model limitation into a false accusation against a compliant pack.
_PLACEHOLDER = re.compile(r"[□■�░-▓]{2,}")


def has_unreadable_glyphs(text: str) -> bool:
    """Did the recogniser hit script it has no characters for?"""
    return _PLACEHOLDER.search(text) is not None


def detect_scripts(text: str) -> list[Script]:
    """Which scripts a string is written in.

    Cheap, deterministic, and the whole basis of the Rule 9(3) check that
    almost nothing in the market is audited against.

    UNREADABLE is reported alongside the scripts we could identify. It means
    "there is writing here we could not transcribe", which is a different claim
    from "this script is absent" -- and the rules engine treats it as such.
    """
    found: list[Script] = []
    if _LATIN.search(text):
        found.append(Script.LATIN)
    if _DEVANAGARI.search(text):
        found.append(Script.DEVANAGARI)
    if has_unreadable_glyphs(text):
        found.append(Script.UNREADABLE)
    return found or [Script.OTHER]


def dominant_script(text: str) -> Script:
    latin = len(_LATIN.findall(text))
    deva = len(_DEVANAGARI.findall(text))
    if deva > latin:
        return Script.DEVANAGARI
    if latin:
        return Script.LATIN
    return Script.OTHER


# ---------------------------------------------------------------------------
# Import / origin signals
# ---------------------------------------------------------------------------

_IMPORT_CUE = re.compile(
    r"(?:imported\s+(?:and\s+)?(?:by|marketed)|importer|imported\s+from|"
    r"country\s+of\s+origin)", re.IGNORECASE
)
_ORIGIN_RE = re.compile(
    r"\b(?:country\s+of\s+origin|made\s+in|product\s+of|origin)\b\s*[:\-]?\s*"
    r"([A-Z][A-Za-z ]{2,30})", re.IGNORECASE
)


def looks_imported(text: str) -> bool:
    explicit = re.search(r"\b(?:imported\s+(?:(?:and\s+)?marketed\s+)?by|importer|imported\s+from)\b", text, re.IGNORECASE)
    origin = parse_country_of_origin(text)
    return bool(explicit or (origin and origin["country"].casefold() not in {"india", "bharat"}))


def parse_country_of_origin(text: str) -> dict[str, Any] | None:
    match = _ORIGIN_RE.search(text)
    if not match:
        return None
    country = re.sub(r"\s+", " ", match.group(1)).strip().title()
    # A single OCR detection can contain several declarations; do not convert
    # "Made in India MRP 120" into the non-existent country "India MRP".
    country = re.split(r"\b(?:MRP|Net|Manufactured|Imported|Packed|Batch|Expiry|Consumer)\b", country, flags=re.IGNORECASE)[0].strip()
    return {"country": country, "raw": match.group(0).strip()}
