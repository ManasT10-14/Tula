"""Evidence-backed supplementary label information, independent of legal verdicts.

Confidence values are transparent extraction heuristics, not calibrated accuracy
probabilities. Missing fields stay missing; date and OCR alternatives are kept.
"""
from __future__ import annotations

import calendar
import copy
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal

from . import layout
from . import normalizers as norm
from .batch import EXTRACTION_CUE as BATCH_EXTRACTION_CUE
from .batch import STANDARD_CUE, joined_cue, parse_batch

BATCH_CUE = STANDARD_CUE  # Public cue contract used by officer corrections.

DATE_CUES = {
    "manufacturing_date": re.compile(r"\b(?:mfg|mfd|manufactured\s+on|manufacturing\s+date|date\s+of\s+manufacture)\b(?!\.?\s*by)|निर्माण\s*(?:तिथि|दिनांक)", re.IGNORECASE),
    "packing_date": re.compile(r"\b(?:pkd|packed\s+(?:on|in)|packing\s+date|date\s+of\s+pack(?:ing)?)\b|पैकिंग\s*(?:तिथि|दिनांक)", re.IGNORECASE),
    "expiry_date": re.compile(r"\b(?:exp(?:iry|ires|iration)?(?:\s+date)?)\b|समाप्ति\s*(?:तिथि|दिनांक)", re.IGNORECASE),
    "best_before": re.compile(r"\bbest\s*(?:before|by)\b", re.IGNORECASE),
    "use_by": re.compile(r"\buse\s*(?:by|before)\b", re.IGNORECASE),
}
INGREDIENT_CUE = re.compile(r"\bingredients?\s*[:\-]?|सामग्री\s*[:\-]?", re.IGNORECASE)
IMPORTER_CUE = re.compile(r"\b(?:imported\s+by|importer)\b", re.IGNORECASE)
HEADING = re.compile(
    r"\b(?:m\.?r\.?p|net\s*(?:wt|weight|qty|quantity)|nutrition|nutritional|"
    r"manufactured|mfg|mfd|packed|pkd|batch|lot|expiry|exp|best\s*before|use\s*by|"
    r"consumer\s*care|customer\s*care|marketed|imported|country\s*of\s*origin|"
    r"ingredients?|storage|directions|warning|allergen|contains?|may\s*contain)\b", re.IGNORECASE,
)
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
_MONTH_NAMES = "|".join(_MONTHS)
_NUMERIC_DATE = re.compile(r"(?<![\d/.-])(\d{1,4})\s*([/.-])\s*(\d{1,4})(?:\s*\2\s*(\d{2,4}))?(?![\d/.-])")
_ALPHA_DATE = re.compile(
    rf"\b(?:(\d{{1,2}})\s*[-/]?\s*)?({_MONTH_NAMES})[a-z]*\.?\s*[-/,]?\s*(\d{{2,4}})\b", re.IGNORECASE,
)
_DURATION = re.compile(r"\b(\d{1,3})\s*(days?|months?|years?)\b", re.IGNORECASE)


def date_candidates(text: str) -> dict | None:
    """Parse printed dates without choosing a day/month order by locale."""
    candidates: list[dict] = []
    match = _ALPHA_DATE.search(text)
    short_year = False
    raw = None

    def add(year: int, month: int, day: int | None, interpretation: str):
        try:
            date(year, month, day or 1)
        except ValueError:
            return
        iso = f"{year:04d}-{month:02d}" + (f"-{day:02d}" if day else "")
        if not any(c["iso"] == iso for c in candidates):
            candidates.append({"iso": iso, "year": year, "month": month, "day": day,
                               "precision": "day" if day else "month", "interpretation": interpretation})

    if match:
        day, month, year_text = match.groups()
        year = int(year_text)
        short_year = len(year_text) == 2
        add(year + 2000 if short_year else year, _MONTHS[month.lower()], int(day) if day else None,
            "named_month")
        raw = match.group(0)
    else:
        match = _NUMERIC_DATE.search(text)
        if not match:
            return None
        first, _, middle, last = match.groups()
        raw = match.group(0)
        a, b = int(first), int(middle)
        if last:
            c = int(last)
            if len(first) == 4:
                add(a, b, c, "year_month_day")
            else:
                short_year = len(last) == 2
                year = c + 2000 if short_year else c
                explicit_dmy = re.search(r"\bdd\s*[/.-]\s*mm\s*[/.-]\s*y{2,4}\b", text, re.IGNORECASE)
                explicit_mdy = re.search(r"\bmm\s*[/.-]\s*dd\s*[/.-]\s*y{2,4}\b", text, re.IGNORECASE)
                if not explicit_mdy:
                    add(year, b, a, "day_month_year")
                if not explicit_dmy:
                    add(year, a, b, "month_day_year")
        elif len(first) == 4:
            add(a, b, None, "year_month")
        else:
            # The two-part expression must be month/year, never a bare day/month.
            short_year = len(middle) == 2
            add(b + 2000 if short_year else b, a, None, "month_year")
    if not candidates and raw is None:
        return None
    # A two-digit year is how Indian packaging prints a year -- "PKD ON:
    # 01-JULY-26" is the norm, not an anomaly -- and `add` above has already
    # resolved the century deterministically to 2000-2099, so nothing is left
    # undecided by it. Holding every such reading for review made the date rule,
    # and every rule gated on dated applicability behind it, permanently
    # inconclusive on essentially every real pack. The assumption stays on the
    # record in `two_digit_year_assumption`, which the report prints, so an
    # officer still sees what was assumed; what does not survive is treating a
    # single unambiguous reading as a conflict. Genuine ambiguity -- 08/09/26,
    # which is two dates -- is `len(candidates) != 1` and still unresolved.
    return {"raw": raw, "candidates": candidates,
            "status": "needs_review" if len(candidates) != 1 else "detected",
            "two_digit_year_assumption": "2000–2099; verify printed year" if short_year else None}


def _date_value(text: str) -> dict | None:
    parsed = date_candidates(text)
    if parsed:
        return parsed
    duration = _DURATION.search(text)
    if duration:
        reference = ("manufacturing_date" if re.search(r"manufactur|\bmfg\b|\bmfd\b", text, re.IGNORECASE)
                     else "packing_date" if re.search(r"pack", text, re.IGNORECASE) else None)
        return {"raw": duration[0], "candidates": [], "status": "relative_duration",
                "duration": int(duration[1]), "unit": duration[2].lower().rstrip("s"),
                "reference": reference}
    return None


def _field(value, raw: str, lines: list[layout.Located], *, method="keyword_pattern", status="detected", candidates=(), decisive=None) -> dict:
    source_spans = [s for panel, line in lines for s in layout.sources(panel, line)]
    # Provenance records every line the block was assembled from; how well it
    # was read is judged on the lines that actually supplied a value. A contact
    # block is swept from beneath a heading, so it collects incidental text -- a
    # licence number at 0.54 under a company name at 0.98 -- and neither that
    # line's score nor a recogniser conflict on it says anything about the name
    # and PIN the declaration rests on. Where no subset is named, every line is
    # decisive and the weakest still governs: a cue joined to its value is two
    # halves of one reading, not a block with a ragged tail.
    judged = decisive if decisive is not None else lines
    judged_spans = [s for panel, line in judged for s in layout.sources(panel, line)] or source_spans
    confidence = min((s["ocr_confidence"] for s in judged_spans), default=0.0)
    conflict = any(getattr(line, "review_required", False) for _, line in judged)
    alternatives = [a for _, line in lines for a in getattr(line, "alternatives", [])]
    field = {"value": value, "raw": raw, "sources": source_spans,
            "ocr_confidence": confidence,
            "extraction_confidence": round(confidence * (0.88 if method == "spatial_keyword_value" else 0.97), 4),
            "method": method, "status": "needs_review" if confidence < 0.55 or conflict else status,
            "candidates": list(candidates), "ocr_alternatives": alternatives}
    if len(lines) == 1 and getattr(lines[0][1], "_text_range", None) is not None:
        field["text_range"] = list(lines[0][1]._text_range)
        field["region_text"] = lines[0][1]._region_text
    return field


def _repeated_cue_regions(located, cue, *, other_cues=()):
    """Literal text windows keep the original region; no glyph box is invented."""
    panel, original = located
    matches = list(cue.finditer(original.text))
    if len(matches) < 2:
        return [located]
    output = []
    for match in matches:
        start = match.start()
        boundaries = [other.start() for pattern in [cue, *other_cues, *DATE_CUES.values()]
                      for other in pattern.finditer(original.text)
                      if other.start() >= match.end()]
        end = min(boundaries, default=len(original.text))
        while end > start and original.text[end - 1].isspace():
            end -= 1
        line = copy.copy(original)
        line.text = original.text[start:end]
        line._text_range = (start, end)
        line._region_text = original.text
        line._source_spans = layout.sources(panel, original)
        line._extraction_method = "repeated_cue_text_window"
        output.append((panel, line))
    return output


def _supplementary_regions(lines, cue, parser, *, other_cues, window_cues=()):
    # Synthetic text windows share one image rectangle. They are not independent
    # spatial peers and must not borrow values from each other's identical box.
    regions = [part for located in lines
               for part in _repeated_cue_regions(located, cue, other_cues=[*other_cues, *window_cues])]
    whole = [located for located in regions if getattr(located[1], "_text_range", None) is None]
    windows = [located for located in regions if getattr(located[1], "_text_range", None) is not None]
    return layout.associated(whole, cue, parser, other_cues=other_cues) + windows


def _segments(text: str, cue: re.Pattern) -> str:
    """Value window ends at the next distinct field cue on the same OCR line."""
    match = cue.search(text)
    value = text[match.end():] if match else text
    boundaries = [pattern.search(value) for pattern in [*DATE_CUES.values(), BATCH_EXTRACTION_CUE]]
    ends = [found.start() for found in boundaries if found]
    return value[:min(ends)] if ends else value


# The Rules require the declaration in Hindi in Devanagari script or in
# English, so a lexicon that reads only English reads only half the labels it
# will be shown. Devanagari terms sit beside their English counterparts in the
# same category: the screen reports the category, not the language it found it
# in. Terms are matched literally with word boundaries, never transliterated.
ALLERGEN_TERMS = {
    "milk": {
        "explicit": ("milk", "milk solids", "milk powder", "dairy",
                     "दूध", "दुग्ध", "मिल्क", "दूध ठोस", "दुग्ध ठोस"),
        "possible": ("casein", "caseinate", "whey", "lactose", "butter", "cheese", "cream", "ghee",
                     "मक्खन", "पनीर", "घी", "क्रीम", "मलाई", "दही", "खोया", "छेना",
                     "कैसीन", "मट्ठा", "लैक्टोज"),
    },
    "peanuts": {
        "explicit": ("peanut", "peanuts", "groundnut", "groundnuts",
                     "मूंगफली", "मुंगफली", "मूँगफली"),
        "possible": (),
    },
    "soy": {
        "explicit": ("soy", "soya", "soybean", "soybeans", "सोया", "सोयाबीन"),
        "possible": ("lecithin", "लेसिथिन"),
    },
    "gluten": {
        "explicit": ("gluten", "ग्लूटेन"),
        # राई carries both rye and mustard seed in Hindi. It stays a "possible"
        # in both categories rather than being forced into one meaning.
        "possible": ("wheat", "barley", "rye", "malt", "semolina", "oats",
                     "गेहूं", "गेहूँ", "गेहू", "जौ", "राई", "माल्ट", "सूजी", "मैदा", "जई",
                     "रवा", "दलिया"),
    },
    "egg": {
        "explicit": ("egg", "eggs", "अंडा", "अंडे", "अण्डा", "अण्डे"),
        "possible": ("albumen", "ovalbumin", "एल्ब्यूमिन"),
    },
    "tree nuts": {
        "explicit": ("almond", "almonds", "cashew", "cashews", "walnut", "walnuts",
                     "pistachio", "hazelnut", "pecan", "brazil nut", "macadamia",
                     "बादाम", "काजू", "अखरोट", "पिस्ता", "हेजलनट", "हेज़लनट"),
        "possible": (),
    },
    "sesame": {
        "explicit": ("sesame", "तिल"),
        "possible": ("tahini", "ताहिनी"),
    },
    "fish": {
        "explicit": ("fish", "salmon", "tuna", "anchovy", "sardine",
                     "मछली", "टूना", "सैल्मन"),
        "possible": (),
    },
    "shellfish": {
        "explicit": ("shellfish", "crustacean", "crustaceans", "shrimp", "prawn", "crab",
                     "lobster", "mussel", "oyster",
                     "झींगा", "झीेंगा", "केकड़ा", "घोंघा", "शंख"),
        "possible": (),
    },
    "mustard": {
        "explicit": ("mustard", "सरसों", "सरसो"),
        "possible": ("राई",),
    },
    "sulphites": {
        "explicit": ("sulphite", "sulfite", "sulphites", "sulfites",
                     "sulphur dioxide", "sulfur dioxide",
                     "सल्फाइट", "सल्फर डाइऑक्साइड", "गंधक"),
        "possible": (),
    },
}
ALLERGEN_BASIS = (
    "Screened against the allergen categories enumerated in the Food Safety and "
    "Standards (Labelling and Display) Regulations, 2020, regulation 5(3) -- "
    "cereals containing gluten, crustacea, milk, egg, fish, peanuts, tree nuts "
    "and sulphites -- matched literally against the ingredient text that was "
    "read, in English and in Hindi written in Devanagari. Sesame and mustard "
    "are screened as well; they are Codex categories, not part of the Indian "
    "schedule."
)
ALLERGEN_DISCLAIMER = (
    "Informational label screening only; not medical advice or a guarantee that a product is safe. "
    "OCR can miss ingredients and cross-contact statements. Verify the complete original label; "
    "a missing match does not establish absence."
)


def _match_concerns(ingredient_fields: list[dict], selected: list[str]) -> list[dict]:
    """Locate each named concern in the preserved ingredient statements.

    One deterministic pass, shared by the officer's own concern list and by the
    unprompted screen. Negation, "-free" claims and plant-milk compounds are
    excluded here so both callers inherit the same exclusions.
    """
    matches = []
    for concern in selected:
        terms = ALLERGEN_TERMS.get(concern, {"explicit": (concern,), "possible": ()})
        for ingredient in ingredient_fields:
            text = str(ingredient.get("value") or ingredient.get("raw") or "")
            for kind, words in terms.items():
                for word in sorted(words, key=len, reverse=True):
                    pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.IGNORECASE)
                    for found in pattern.finditer(text):
                        context = text[max(0, found.start() - 60):found.end() + 25]
                        prefix = text[max(0, found.start() - 50):found.start()]
                        if concern == "milk" and (
                            (word == "milk" and re.search(r"\b(?:coconut|almond|oat|soya?|rice|cashew)\s+$", prefix, re.IGNORECASE))
                            or (word == "butter" and re.search(r"\b(?:cocoa|peanut|almond|shea)\s+$", prefix, re.IGNORECASE))
                        ):
                            continue
                        # English negates before the noun and Hindi after it:
                        # "gluten free" and "ग्लूटेन रहित" mean the same thing.
                        if (re.search(r"\b(?:no|without|free\s+from|does\s+not\s+contain|contains?\s+no)\s+(?:added\s+)?$", prefix, re.IGNORECASE)
                                or re.search(r"(?:रहित|मुक्त|नहीं)\s*$", prefix)
                                or re.match(r"\s*[- ]free\b", text[found.end():], re.IGNORECASE)
                                or re.match(r"\s*(?:रहित|मुक्त)\b", text[found.end():])):
                            continue
                        # The danda ends a Hindi sentence the way a full stop
                        # ends an English one; without it the whole line reads
                        # as one clause and a cross-contact cue leaks across.
                        boundary = r"[.;\n।॥]"
                        sentence_prefix = re.split(boundary, text[:found.start()])[-1]
                        # English puts the qualifier before the allergen -- "may
                        # contain peanuts" -- and Hindi after it, "मूंगफली के अंश
                        # हो सकते हैं". Both sides of the clause are read.
                        sentence_suffix = re.split(boundary, text[found.end():])[0]
                        contact = bool(
                            re.search(
                                r"may\s+contain|traces?\s+of|facilit|factory|premises|"
                                r"shared\s+equipment|same\s+(?:line|equipment)|cross.contact|"
                                r"(?:manufactured|produced|processed|packed|made)\s+(?:in|on)\b|"
                                r"also\s+(?:handles|processes|uses|contains)",
                                sentence_prefix, re.IGNORECASE)
                            or re.search(r"अंश|निशान|हो\s*सकत|संपर्क|सुविधा|कारखान",
                                         sentence_prefix + " " + sentence_suffix)
                        )
                        evidence_kind = "cross_contact" if contact else kind
                        key = (concern, evidence_kind, ingredient.get("raw"), found.start())
                        if any(m["_key"] == key for m in matches):
                            continue
                        matches.append({"concern": concern, "matched_text": found[0],
                                        "context": context, "kind": evidence_kind,
                                        "statement": ingredient.get("method") == "allergen_statement",
                                        "sources": ingredient.get("sources", []),
                                        "ocr_confidence": ingredient.get("ocr_confidence", 0),
                                        "extraction_confidence": ingredient.get("extraction_confidence", 0),
                                        "status": ingredient.get("status", "needs_review"), "_key": key})
    for match in matches:
        match.pop("_key", None)
    return matches


def analyze_allergens(ingredient_fields: list[dict], concerns: Iterable[str] = ()) -> dict:
    """Match a selectable/custom concern against preserved ingredient statements."""
    aliases = {"peanut": "peanuts", "soya": "soy", "eggs": "egg", "nuts": "tree nuts", "sulfites": "sulphites"}
    if isinstance(concerns, str):
        concerns = concerns.split(",")
    selected = list(dict.fromkeys(aliases.get(str(c).strip().lower(), str(c).strip().lower())
                                 for c in concerns if str(c).strip()))[:30]
    matches = _match_concerns(ingredient_fields, selected)
    return {"concerns": selected, "matches": matches, "disclaimer": ALLERGEN_DISCLAIMER,
            "unmatched": [c for c in selected if not any(m["concern"] == c for m in matches)],
            "available_concerns": list(ALLERGEN_TERMS),
            "screen": screen_allergens(ingredient_fields)}


def screen_allergens(ingredient_fields: list[dict]) -> dict:
    """Report every allergen the ingredient text names, without being asked.

    The officer-directed search answers "is milk in this?". This answers the
    question an officer actually has in front of a shelf -- "what is in this,
    and does the label say so?" -- by sweeping the whole schedule rather than a
    typed list.

    The sweep is a closed lexicon because the duty it screens is a closed list.
    FSS (Labelling and Display) Regulations 2020, regulation 5(3) enumerates
    the allergens a food label must declare; a fixed vocabulary is the right
    shape for a fixed schedule, it cites the ingredient text it matched, and it
    returns the same answer for the same label every time. A language model
    would add paraphrase coverage and take away all three of those properties.

    Nothing here is a Legal Metrology finding. Allergen declaration is a food
    safety duty, not a packaged-commodities one, so this is reported as a
    referral: it tells an inspector what to send to the food safety authority.
    """
    matches = _match_concerns(ingredient_fields, list(ALLERGEN_TERMS))
    present: dict[str, dict] = {}
    for match in matches:
        current = present.get(match["concern"])
        # Explicit beats possible beats cross-contact: an ingredient that names
        # the allergen outright is stronger evidence than a "may contain".
        rank = {"explicit": 3, "possible": 2, "cross_contact": 1}
        if current is None or rank[match["kind"]] > rank[current["kind"]]:
            present[match["concern"]] = match

    declared = {m["concern"] for m in matches if m["statement"]}
    detected = []
    for concern, match in sorted(present.items()):
        detected.append({
            "allergen": concern,
            "kind": match["kind"],
            "matched_text": match["matched_text"],
            "context": match["context"],
            "declared_in_statement": concern in declared,
            "sources": match["sources"],
            "ocr_confidence": match["ocr_confidence"],
            "status": match["status"],
        })
    # An allergen named in the ingredient list but absent from any "Contains"
    # statement is the pattern worth an officer's attention. Only explicit
    # matches qualify: "lecithin" may or may not be soy, and a referral built
    # on a maybe wastes somebody's afternoon.
    undeclared = [
        item["allergen"] for item in detected
        if item["kind"] == "explicit" and not item["declared_in_statement"]
    ]
    return {
        "detected": detected,
        "declared_in_statement": sorted(declared),
        "undeclared": undeclared,
        "screened_against": list(ALLERGEN_TERMS),
        "basis": ALLERGEN_BASIS,
        "disclaimer": ALLERGEN_DISCLAIMER,
        "referral": (
            "Allergen declaration is a food safety duty under FSS (Labelling and "
            "Display) Regulations 2020 reg. 5(3), not a Legal Metrology "
            "(Packaged Commodities) duty. Nothing here is a finding under the "
            "Rules; refer it to the food safety authority."
        ) if detected else "",
    }


def extract_intelligence(lines: list[layout.Located], *, allergen_concerns: Iterable[str] = (), as_of: date | None = None) -> dict:
    fields: dict[str, list[dict]] = {}
    warnings = []
    when = as_of or datetime.now(UTC).date()
    numeric_fields = {
        "retail_sale_price": (re.compile(r"\bm\.?\s*r\.?\s*p\.?|maximum\s+retail\s+price", re.IGNORECASE), norm.parse_price),
        "net_quantity": (re.compile(r"\bnet\s*(?:qty|quantity|wt\.?|weight|vol(?:ume)?|contents?)\b|शुद्ध|वजन", re.IGNORECASE), norm.parse_net_quantity),
        "unit_sale_price": (re.compile(r"\bunit\s*(?:sale\s*)?price\b", re.IGNORECASE), norm.parse_unit_price),
    }
    for name, (cue, parser) in numeric_fields.items():
        expanded = (_supplementary_regions(lines, cue, parser, other_cues=[HEADING], window_cues=[BATCH_EXTRACTION_CUE])
                    if name == "net_quantity" else layout.associated(lines, cue, parser, other_cues=[HEADING]))
        for located in expanded:
            line = located[1]
            if not cue.search(line.text):
                continue
            parsed = parser(line.text)
            if not parsed and not getattr(line, "_text_range", None) and any(layout.source(*located) in layout.sources(*other)
                                  and other[1] is not line and parser(other[1].text)
                                  for other in expanded):
                continue
            alternatives = []
            for alternative in getattr(line, "alternatives", []):
                candidate = parser(alternative.get("text", ""))
                if candidate:
                    alternatives.append({"value": candidate, "raw": alternative.get("text"),
                                         "ocr_confidence": alternative.get("confidence"), "variant": alternative.get("variant")})
            fields.setdefault(name, []).append(_field(parsed, line.text, [located],
                method=getattr(line, "_extraction_method", "keyword_pattern"),
                status="detected" if parsed else "needs_review", candidates=alternatives))
    for name, cue in DATE_CUES.items():
        expanded = _supplementary_regions(lines, cue, lambda t, cue=cue: _date_value(_segments(t, cue)),
                                          other_cues=[*DATE_CUES.values(), BATCH_EXTRACTION_CUE], window_cues=[HEADING])
        values = []
        for located in expanded:
            _, line = located
            if not cue.search(line.text):
                continue
            parsed = _date_value(_segments(line.text, cue))
            # Keep an unreadable cue only if no associated value was found.
            if not parsed and not getattr(line, "_text_range", None) and any(layout.source(*located) in layout.sources(*other)
                                  and other[1] is not line
                                  and _date_value(_segments(other[1].text, cue))
                                  for other in expanded):
                continue
            candidate = parsed or {"status": "needs_review", "candidates": []}
            value = (candidate["candidates"][0]["iso"] if len(candidate["candidates"]) == 1 else None)
            if candidate.get("duration") is not None:
                value = {k: candidate[k] for k in ("duration", "unit", "reference")}
            field = _field(value, line.text, [located], method=getattr(line, "_extraction_method", "keyword_pattern"),
                           status=candidate["status"], candidates=candidate["candidates"])
            if candidate.get("two_digit_year_assumption"):
                field["warning"] = candidate["two_digit_year_assumption"]
            if name in {"expiry_date", "best_before", "use_by"} and field["status"] == "detected" and value:
                d = candidate["candidates"][0]
                end = date(d["year"], d["month"], d["day"] or calendar.monthrange(d["year"], d["month"])[1])
                field["temporal_status"] = "printed_date_passed" if end < when else "printed_date_not_passed"
                field["as_of"] = when.isoformat()
            values.append(field)
        if values:
            fields[name] = _deduplicate(values)

    expanded = layout.associated(lines, BATCH_EXTRACTION_CUE, parse_batch,
                                 other_cues=[*DATE_CUES.values(), HEADING])
    for located in expanded:
        panel, line = located
        value = parse_batch(line.text)
        if value:
            # The layout helper copies the value line. Recover both originals
            # so a disputed cue cannot lose its OCR alternatives on joining.
            spans = layout.sources(panel, line)
            originals = [source for source in lines if layout.same_surface(located, source)
                         and any(layout.source(*source) == span for span in spans)] or [located]
            alternatives = []
            for _, source in originals:
                for alternative in getattr(source, "alternatives", []):
                    candidate = parse_batch(alternative.get("text", ""))
                    if candidate:
                        alternatives.append({"value": candidate, "raw": alternative.get("text"),
                                             "ocr_confidence": alternative.get("confidence"), "variant": alternative.get("variant")})
            field = _field(value, line.text, originals,
                method=getattr(line, "_extraction_method", "joined_batch_cue" if joined_cue(line.text) else "keyword_pattern"),
                candidates=alternatives)
            batches = fields.setdefault("batch_number", [])
            repeated = next((item for item in batches if item["value"] == value and item["raw"] == line.text), None)
            if repeated:
                repeated["ocr_confidence"] = min(repeated["ocr_confidence"], field["ocr_confidence"])
                repeated["extraction_confidence"] = min(repeated["extraction_confidence"], field["extraction_confidence"])
                if field["status"] == "needs_review":
                    repeated["status"] = "needs_review"
                for key in ("sources", "ocr_alternatives", "candidates"):
                    repeated[key].extend(item for item in field[key] if item not in repeated[key])
            else:
                batches.append(field)

    ingredient_fields = []
    for located in lines:
        if INGREDIENT_CUE.search(located[1].text):
            block = layout.text_block(lines, located, stop=HEADING, limit=12)
            raw = "\n".join(line.text for _, line in block)
            value = INGREDIENT_CUE.sub("", raw, count=1).strip(" :;-\n")
            # Do not consume inline date/MRP/other sections in a fused OCR box.
            boundary = HEADING.search(value)
            if boundary:
                value = value[:boundary.start()].strip(" ;,\n")
            if value:
                ingredient_fields.append(_field(value, raw, block, method="ingredient_block"))
        elif (re.search(r"\b(?:contains?|may\s+contain|allergen\s*(?:advice|information))\b", located[1].text, re.IGNORECASE)
              or re.search(r"एलर्ज|इसमें\s*(?:शामिल|है)|अंश\s*हो", located[1].text)):
            ingredient_fields.append(_field(located[1].text, located[1].text, [located], method="allergen_statement"))
    if ingredient_fields:
        fields["ingredients"] = _deduplicate(ingredient_fields)

    for located in lines:
        if IMPORTER_CUE.search(located[1].text):
            block = layout.text_block(lines, located, stop=HEADING, limit=5)
            raw = "\n".join(line.text for _, line in block)
            fields.setdefault("importer", []).append(_field(raw, raw, block, method="contact_block"))
    for key, values in fields.items():
        fields[key] = _deduplicate(values)
        distinct = {_semantic_value(key, value["value"]) for value in values if value["value"] is not None}
        if len(distinct) > 1 and key not in {"ingredients", "importer"}:
            warnings.append(f"Multiple {key.replace('_', ' ')} values were detected; verify the original markings.")
            for value in fields[key]:
                value["status"] = "needs_review"
                value.pop("temporal_status", None)
                value.pop("as_of", None)
    additives = []
    for item in ingredient_fields:
        for match in re.finditer(r"\b(?:INS\s*[-:]?\s*\d{3,4}[a-z]?|E\s*-?\s*\d{3,4}[a-z]?)\b", str(item["value"]), re.IGNORECASE):
            additives.append({"code": match[0], "sources": item["sources"], "method": "printed_additive_identifier"})
    dietary = []
    for located in lines:
        if re.search(r"\b(?:non[- ]vegetarian|vegetarian|vegan|gluten[- ]free|lactose[- ]free|sugar[- ]free)\b", located[1].text, re.IGNORECASE):
            dietary.append(_field(located[1].text, located[1].text, [located], method="printed_dietary_claim"))
    return {"fields": fields, "allergens": analyze_allergens(ingredient_fields, allergen_concerns),
            "additives": additives, "dietary": dietary, "product": product_intelligence(lines),
            "warnings": warnings, "confidence_note": "Extraction scores are pattern and geometry heuristics, not calibrated accuracy probabilities."}


def _deduplicate(values: list[dict]) -> list[dict]:
    """Repeated readings retain the least certain evidence, regardless of order."""
    output: list[dict] = []
    for value in values:
        match = next((item for item in output if item["value"] == value["value"]
                      and item["raw"] == value["raw"]
                      and item.get("text_range") == value.get("text_range")
                      and item.get("region_text") == value.get("region_text")), None)
        if match:
            for key in ("sources", "ocr_alternatives", "candidates"):
                match.setdefault(key, [])
                match[key].extend(item for item in value.get(key, []) if item not in match[key])
            for key in ("ocr_confidence", "extraction_confidence"):
                scores = [match.get(key), value.get(key)]
                match[key] = min(scores) if all(type(score) in (int, float) for score in scores) else None
            if value.get("status") == "needs_review" or match.get("status") == "needs_review":
                match["status"] = "needs_review"
                match.pop("temporal_status", None)
                match.pop("as_of", None)
            for key in ("warning", "review_reason"):
                notes = sorted({str(item[key]) for item in (match, value) if item.get(key)})
                if notes:
                    match[key] = " ".join(notes)
        else:
            output.append(value)
    return output


def _semantic_value(name, value):
    """Compare a field's meaning, excluding raw spelling and source metadata."""
    if isinstance(value, dict):
        if name == "net_quantity":
            amount = (Decimal(str(value["value"])) * Decimal(str(value["factor"]))
                      if value.get("value") is not None and value.get("factor") is not None
                      else value.get("value_base"))
            return (value.get("unit_base"), amount)
        if name == "unit_sale_price":
            unit = norm.CANONICAL_UNITS.get(value.get("per_unit"))
            denominator = (Decimal(str(value["per_value"])) * Decimal(str(unit[1]))
                           if unit and value.get("per_value") is not None else value.get("per_base"))
            return (value.get("unit_base"), denominator, value.get("value"))
        if name == "retail_sale_price":
            return (value.get("currency"), tuple(value.get("distinct_values", [])))
    return str(value)


_CATEGORIES = {
    "personal_care": ("shampoo", "soap", "toothpaste", "hair oil", "handwash", "face cream", "moisturiser"),
    "household": ("detergent", "floor cleaner", "phenyl", "washing powder", "dishwash"),
    "industrial": ("cement", "fertilizer", "fertiliser", "paint"),
    "food": ("biscuits", "biscuit", "cookies", "namkeen", "chips", "noodles", "pasta", "rice", "atta", "flour", "dal", "pulses", "sugar", "salt", "tea", "coffee", "milk", "ghee", "butter", "cheese", "curd", "edible oil", "spices", "masala", "pickle", "jam", "honey", "chocolate", "candy", "juice", "beverage", "drinking water"),
}


def product_intelligence(lines: list[layout.Located]) -> dict:
    evidence = []
    categories = set()
    for located in lines:
        text = located[1].text
        if len(text.split()) > 7 or HEADING.search(text) or located[1].confidence < 0.55:
            continue
        for category, terms in _CATEGORIES.items():
            if any(re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE) for term in terms):
                categories.add(category)
                evidence.append(_field(category, text, [located], method="product_name_lexicon"))
    category = next(iter(categories)) if len(categories) == 1 else "unknown"
    origin_evidence = []
    origins = set()
    for located in lines:
        text = located[1].text
        origin = norm.parse_country_of_origin(text)
        if IMPORTER_CUE.search(text):
            origins.add("imported")
            origin_evidence.append(_field("imported", text, [located], method="explicit_importer"))
        if origin:
            state = "domestic" if origin["country"].casefold() in {"india", "bharat"} else "foreign_origin_declared"
            origins.add(state)
            origin_evidence.append(_field(state, text, [located], method="printed_country_of_origin"))
    origin_state = next(iter(origins)) if len(origins) == 1 else "needs_review" if origins else "unknown"
    if origins == {"imported", "foreign_origin_declared"}:
        origin_state = "imported"
    if any(item["status"] == "needs_review" for item in origin_evidence):
        origin_state = "needs_review"
    packages = []
    for located in lines:
        match = re.search(r"\b(?:bottle|pouch|sachet|carton|jar|tin|tube)\b", located[1].text, re.IGNORECASE)
        if match:
            packages.append(_field(match[0].lower(), located[1].text, [located], method="printed_package_reference", status="needs_review"))
    return {"category": category,
            "food_nonfood": "food" if category == "food" else "non_food" if category != "unknown" else "unknown",
            "category_evidence": evidence, "origin": origin_state, "origin_evidence": origin_evidence,
            "package_type_evidence": packages,
            "applicability_status": "requires_officer_confirmation",
            "note": "Text-based category suggestions do not establish legal exemptions or certify contents. Package references are not visual material classification."}
