"""Turning recognised text into located, normalised declarations.

The job here is not "find the words". It is to decide which line on which panel
*is* the net quantity declaration, what it means once normalised, and which
scripts it was printed in -- because those three facts are what Rules 6, 8 and
9 are actually about.

Cue matching runs in Hindi as well as English throughout. A label that declares
"शुद्ध वजन 200 ग्राम" and nothing in English is Rule 9(3)-compliant in the
opposite direction, and an English-only cue list would score it as having no
net quantity declaration at all.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..domain.enums import DeclarationClass as DC
from ..domain.enums import ExtractionPath, Panel, Script
from ..domain.models import Declaration, EvidenceCoverage, TextSpan
from ..ocr.base import OcrLine, OcrResult
from . import context, layout
from . import normalizers as norm
from .intelligence import (
    DATE_CUES,
    _deduplicate,
    _field,
    _repeated_cue_regions,
    _semantic_value,
    date_candidates,
    extract_intelligence,
)
from .provenance import attach, get_provenance, intelligence_record

# One source of truth for "this line was actually read", shared with the
# coverage model that consumes the count.
LEGIBLE_CONFIDENCE = EvidenceCoverage.LEGIBLE_CONFIDENCE

# Recognition confidence falls in three bands, not two. Text at or above
# LEGIBLE_CONFIDENCE may establish a finding. Text below this floor is noise and
# is not adjudicated at all. Between them sits the band this constant exists to
# rescue: faint but unambiguous print, which is what variable data looks like
# when it is inkjet-coded onto foil. A pack's real "400g" scoring 0.540 was
# being deleted outright while the offset-printed nutrition table beside it
# scored 0.9, so the most prominent quantity left in the capture was a
# nutrition cell. These readings are admitted and held for review instead.
CANDIDATE_CONFIDENCE = 0.35

# --------------------------------------------------------------------------
# Cues, in both scripts
# --------------------------------------------------------------------------

CUES: dict[DC, re.Pattern] = {
    DC.NET_QUANTITY: re.compile(
        r"net\s*(?:qty|quantity|wt\.?|weight|vol\.?|volume|content)|"
        r"शुद्ध|वजन|मात्रा|भार|निवल",
        re.IGNORECASE,
    ),
    DC.RETAIL_SALE_PRICE: re.compile(
        r"m\.?\s*r\.?\s*p\.?|maximum\s+retail\s+price|retail\s+sale\s+price|"
        r"अधिकतम|खुदरा|मूल्य|कीमत",
        re.IGNORECASE,
    ),
    DC.UNIT_SALE_PRICE: re.compile(
        r"unit\s*(?:sale\s*)?price|प्रति\s*इकाई|इकाई\s*मूल्य", re.IGNORECASE
    ),
    DC.DATE_OF_PACKING: re.compile(
        r"\b(?:mfg|mfd)(?!\.?\s*by)|manufactur(?:ed|ing)(?!\s*(?:by|&|and\s+marketed))|"
        r"packed\s+(?:on|in)|date\s+of\s+pack|pkd|month\s+and\s+year|"
        r"निर्माण|पैकिंग|तिथि|दिनांक",
        re.IGNORECASE,
    ),
    # Every inter-word gap here is optional. A recogniser reading small print
    # over a printed rule returns "Manufactured&Marketedby" as one token, and a
    # cue that insists on the spaces finds no manufacturer on a pack that names
    # one in 24-point type.
    DC.MANUFACTURER: re.compile(
        r"manufactured\s*(?:(?:&|and)\s*marketed\s*)?by|mfd\.?\s*by|packed\s*by|imported\s*by|"
        r"manufacturer|packer|निर्मित|निर्माता|पैकर",
        re.IGNORECASE,
    ),
    DC.CONSUMER_CARE: re.compile(
        r"consumer\s*care|customer\s*care|consumer\s*complaint|for\s*complaints|"
        r"help\s*line|toll\s*free|grievance|"
        r"उपभोक्ता|शिकायत|सेवा|हेल्पलाइन",
        re.IGNORECASE,
    ),
    DC.COUNTRY_OF_ORIGIN: re.compile(
        r"\b(?:country\s+of\s+origin|made\s+in|product\s+of|origin)\b|मूल\s*देश|निर्मित\s*देश", re.IGNORECASE
    ),
}

# Generic commodity names, deliberately Indian-retail-shaped. Rule 6(1)(b) is
# about the consumer knowing what the thing *is*, so the lexicon is the honest
# way to test "is this a generic name or just the brand".
GENERIC_LEXICON = (
    "biscuit", "biscuits", "cookies", "rusk", "namkeen", "bhujia", "chips",
    "wafers", "puffcorn", "noodles", "vermicelli", "pasta", "atta", "wheat flour", "maida",
    "besan", "suji", "rava", "rice", "basmati rice", "poha", "dal", "pulses",
    "toor dal", "moong dal", "chana dal", "sugar", "jaggery", "salt", "tea",
    "green tea", "coffee", "milk", "milk powder", "ghee", "butter", "cheese",
    "paneer", "curd", "yoghurt", "edible oil", "mustard oil", "sunflower oil",
    "groundnut oil", "refined oil", "spices", "masala", "turmeric", "chilli powder",
    "coriander powder", "garam masala", "pickle", "jam", "honey", "ketchup",
    "sauce", "chocolate", "candy", "confectionery", "ice cream", "soap",
    "bathing soap", "detergent", "detergent powder", "washing powder", "shampoo",
    "hair oil", "toothpaste", "tooth powder", "handwash", "sanitizer",
    "face cream", "moisturiser", "talcum powder", "agarbatti", "incense sticks",
    "phenyl", "floor cleaner", "cement", "fertilizer", "fertiliser", "paint",
    "mineral water", "packaged drinking water", "soft drink", "juice", "beverage",
)

# Phrases that turn a commodity word into marketing copy rather than a
# declaration. "Made with quality spices" on a noodle packet contains the word
# "spices", and a bare substring search will happily report it as the Rule
# 6(1)(b) generic name -- passing the rule on a pack that never declares what
# the commodity is. This is the false *pass* that mirrors the false accusation:
# both come from treating a string match as a declaration.
# Whitespace is optional throughout: a recogniser routinely returns "MADeWiTH"
# for "MADE WITH", and a pattern demanding a space would miss it.
GENERIC_DISQUALIFIERS = re.compile(
    r"\b(?:made\s*with|make[sd]?\s*with|contains?|containing|enriched\s*with|"
    r"rich\s*in|goodness\s*of|blended\s*with|no\s*added|free\s*from|without|"
    r"with\s*real|with\s*the|source\s*of|flavou?r(?:ed)?\s*with|infused\s*with|"
    r"traces\s*of|may\s*contain|prepared\s*with|ingredients?)\b",
    re.IGNORECASE,
)

# A generic name is a short declaration, not a sentence. Anything longer than
# this many words around the matched term is prose.
GENERIC_MAX_WORDS = 6


@lru_cache(maxsize=512)
def _term_pattern(term: str) -> re.Pattern:
    """Word-boundary matcher for a lexicon term.

    A plain substring test finds "masala" inside "emasala" -- which is what a
    recogniser returns for "...favourite masala taste" when it welds the
    preceding word on. The pack was then reported as declaring a commodity named
    "masala" on the strength of a misread. Commodity names are words, so match
    them as words.
    """
    return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)

GTIN_RE = re.compile(r"\b(\d{8}|\d{12,14})\b")


_LEADING_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _alternatives_agree(line) -> bool:
    """Do the recognisers differ about this line's value, or only about what
    trails it?

    One variant reading "400g 20:25 M3s" against another reading "400g 20125M3s"
    disagrees about an inkjet time code, not about the net quantity, and both
    scored 0.91 -- the arbitrated line score of 0.54 reflects the tail, not the
    number. A variant reading "MRP 160" against "MRP 180" disagrees about the
    number that matters, and must never be shown as though it were settled.
    """
    def leading(text: str):
        found = _LEADING_NUMBER.search(text or "")
        return found.group(0).replace(",", ".") if found else None

    # The line's own reading is one of the competing opinions, and a fixture may
    # list only the rival in `alternatives`.
    values = {leading(line.text)}
    for alternative in getattr(line, "alternatives", None) or []:
        values.add(leading(alternative.get("text", "")))
    return len(values) <= 1


@dataclass
class Extraction:
    declarations: dict[DC, Declaration] = field(default_factory=dict)
    spans: list[TextSpan] = field(default_factory=list)
    full_text: str = ""
    gtin_candidates: list[str] = field(default_factory=list)
    is_imported: bool = False
    warnings: list[str] = field(default_factory=list)
    # How much was actually read. Feeds EvidenceCoverage, which decides whether
    # "not found" may be reported as "not declared".
    lines_read: int = 0
    legible_lines: int = 0
    mean_confidence: float = 0.0
    intelligence: dict = field(default_factory=dict)


# --------------------------------------------------------------------------


def _scripts_for(lines: list[tuple[Panel, OcrLine]], test) -> list[Script]:
    """Which scripts a declaration was printed in, across every panel.

    Any line that either carries the cue or carries the value counts, so a
    Hindi cue line beside a Latin numeral still registers Devanagari coverage.

    The label-wide unreadable check is the important part. A recogniser without
    Devanagari still *detects* the Hindi text box and emits placeholder glyphs
    for it -- but those placeholders match no cue and parse as no value, so the
    line would otherwise be dropped entirely and the pack reported as
    English-only. Any untranscribable text anywhere on the label could be the
    declaration we are looking for, so its presence makes coverage undecidable
    rather than absent.
    """
    found: set[Script] = set()
    for _, line in lines:
        if test(line.text):
            found.update(norm.detect_scripts(line.text))
    if any(norm.has_unreadable_glyphs(line.text) for _, line in lines):
        found.add(Script.UNREADABLE)
    return sorted(found, key=lambda s: s.value)


def _best_line(
    lines: list[tuple[Panel, OcrLine]],
    cue: re.Pattern | None,
    value_test,
) -> tuple[Panel, OcrLine] | None:
    """Prefer a line carrying both the cue and the value, then either alone.

    Ties break on prominence, not on document order. A label often carries the
    same declaration twice -- once in Devanagari, once in Latin -- and picking
    whichever happened to be recognised first would make the extracted value
    depend on scan order.
    """
    def _confident_first(group):
        sure = [(p, l) for p, l in group
                if not getattr(l, "_low_confidence", False)
                and not getattr(l, "review_required", False)]
        return sure or group

    with_both = [(p, l) for p, l in lines if value_test(l.text) and (cue and cue.search(l.text))]
    if with_both:
        return max(_confident_first(with_both), key=_reading_rank)
    with_value = [(p, l) for p, l in lines if value_test(l.text)]
    if with_value:
        # The fallback below takes the most prominent number left on the panel,
        # which is right for a front-of-pack "500 g" with no keyword anywhere.
        # It is wrong across frames: the cue sits on the declarations panel and
        # the boldest number in the capture is a nutrition cell on the back. So
        # when a cue was seen, keep the fallback on the surfaces that carry it.
        if cue is not None:
            cued = {getattr(l, "_capture_index", None)
                    for _, l in lines if cue.search(l.text)}
            if cued:
                near = [(p, l) for p, l in with_value
                        if getattr(l, "_capture_index", None) in cued]
                if near:
                    with_value = near
        return max(_confident_first(with_value), key=_reading_rank)
    if cue is not None:
        with_cue = [(p, l) for p, l in lines if cue.search(l.text)]
        if with_cue:
            return max(with_cue, key=lambda pl: (pl[1].confidence, _reading_rank(pl)))
    return None


def _reading_rank(located):
    line = located[1]
    return (line.height_px or 0, line.confidence, line.text.casefold(),
            str(line.frame or ""), tuple(line.bbox))


def _observation(located, value, originals, *, role=None, decisive_text=None):
    """Recover uncertainty/alternatives from every original in a joined block."""
    panel, line = located
    spans = layout.sources(panel, line)
    contributors = [item for item in originals if layout.same_surface(located, item)
                    and layout.source(*item) in spans] or [located]
    decisive = None
    if decisive_text:
        decisive = [item for item in contributors
                    if any(text and text in item[1].text for text in decisive_text)] or None
    item = _field(value, line.text, contributors,
                  method=getattr(line, "_extraction_method", "keyword_pattern"),
                  status="detected" if value else "cue_only", decisive=decisive)
    item["sources"] = [span for index, span in enumerate(spans) if span not in spans[:index]]
    if role:
        item["role"] = role
    if getattr(line, "_text_range", None) is not None:
        item["text_range"] = list(line._text_range)
        item["region_text"] = line._region_text
    return item


def _primary_readings(lines, cue, parser, *, named_only=False, split_repeated=False):
    candidates = ([part for located in lines for part in _repeated_cue_regions(located, cue, other_cues=CUES.values())]
                  if split_repeated else lines)
    expanded = layout.associated(candidates, cue, parser, other_cues=list(CUES.values()))
    output = []
    for located in expanded:
        _, line = located
        value = parser(line.text)
        if not cue.search(line.text) and (named_only or not value):
            continue
        if not value and not getattr(line, "_text_range", None) and any(layout.source(*located) in layout.sources(*other)
                             and other[1] is not line and parser(other[1].text)
                             for other in expanded):
            continue
        output.append(_observation(located, value, lines))
    return _deduplicate(output)


def _reconcile(declaration, observations, signature, *, clear, reason):
    """A prominent reading may be a display anchor, never a conflict resolver."""
    if declaration is None or not observations:
        return
    observations = sorted(observations, key=lambda item: (item.get("role", ""), item["raw"],
        tuple((str(s.get("frame") or ""), tuple(s.get("bbox") or ())) for s in item["sources"])))
    resolved = [item for item in observations if item["value"]]
    values = {signature(item["value"]) for item in resolved}
    uncertain = any(item["status"] == "needs_review" or (item["value"].get("count") or 0) > 1
                    for item in resolved)
    conflicting = len(values) > 1
    if not declaration.norm and not resolved:
        # Retain the public cue-only norm={} contract.
        attach(declaration, [s for item in observations for s in item["sources"]],
               method=get_provenance(declaration).method)
        return
    declaration.norm["observations"] = observations
    if conflicting or uncertain:
        # Keep the existing compact single-name candidate API. Its complete
        # uncertainty and source records are retained in observations above.
        previous = declaration.norm.get("candidates") if not conflicting else None
        declaration.norm.update(requires_review=True, review_reason=reason,
            candidates=previous or [{**item["value"], "raw": item["raw"], "sources": item["sources"],
                         "status": item["status"], "ocr_alternatives": item["ocr_alternatives"]}
                         | ({"text_range": item["text_range"], "region_text": item["region_text"]}
                            if "text_range" in item else {})
                        for item in resolved])
        for key in clear:
            declaration.norm[key] = None
    # A cue-only fragment is not a competing value, but remains in the record.
    # Only contributors to resolved readings determine the accepted field score.
    contributing = resolved or observations
    provenance = get_provenance(declaration)
    attach(declaration, [*provenance.sources, *[s for item in contributing for s in item["sources"]]],
           method=provenance.method)
    if conflicting or uncertain:
        declaration.raw = "\n".join(dict.fromkeys(item["raw"] for item in observations))


def _price_corroborates_quantity(declarations, quantity) -> str | None:
    """Does the label's own arithmetic identify this number as the quantity?

    Rule 6(11) makes the unit sale price the retail price divided by the net
    quantity, so a pack that prints all three has stated the same fact twice.
    When the arrows on a label point at the wrong row, that redundancy is what
    a person uses to work out which number is which -- MRP 135 over a printed
    0.34 per gram can only be a 400 g pack -- and it is available to the
    machine on exactly the same terms.

    The declared unit price is rounded to two decimals, so it stands for an
    interval, and the quantity is confirmed when it falls inside the interval
    that rounding admits. Nothing here decides a rule: it decides only which
    printed number the net-quantity declaration refers to.
    """
    price = getattr(declarations.get(DC.RETAIL_SALE_PRICE), "norm", {}) or {}
    unit_price = getattr(declarations.get(DC.UNIT_SALE_PRICE), "norm", {}) or {}
    if price.get("requires_review") or unit_price.get("requires_review"):
        return None
    mrp = price.get("value")
    per_value, per_base = unit_price.get("value"), unit_price.get("per_base")
    value_base, unit_base = quantity.get("value_base"), quantity.get("unit_base")
    if not all(isinstance(v, (int, float)) and v > 0
               for v in (mrp, per_value, per_base, value_base)):
        return None
    if unit_base != unit_price.get("unit_base"):
        return None
    # Half a unit in the last printed decimal place, either way.
    half_step = 0.5 * 10 ** -_decimals(per_value)
    low, high = per_value - half_step, per_value + half_step
    if low <= 0:
        return None
    implied_low = (mrp / high) * per_base
    implied_high = (mrp / low) * per_base
    if not implied_low <= value_base <= implied_high:
        return None
    return (f"the printed unit price of {per_value:g} per {per_base:g} "
            f"{unit_price.get('unit_base')} against an MRP of {mrp:g} implies a net "
            f"quantity between {implied_low:.0f} and {implied_high:.0f} "
            f"{unit_base}, which contains the {value_base:g} {unit_base} read here")


def _decimals(value: float) -> int:
    text = f"{value!r}"
    return len(text.partition(".")[2]) if "." in text else 0


def _quantity_pool(lines):
    return [located for located in lines
            if context.quantity_allowed(lines, located, CUES[DC.NET_QUANTITY])
            and (CUES[DC.NET_QUANTITY].search(located[1].text)
                 or not re.search(r"\b(?:gross|drained|shipping)\s*(?:wt|weight|mass)\b",
                                  located[1].text, re.IGNORECASE))]


def _date_readings(lines):
    """Manufacturing and packing are separate events, including on one line."""
    output = []
    for role, cue in DATE_CUES.items():
        if role not in {"manufacturing_date", "packing_date"}:
            continue
        regions = [part for located in lines for part in _repeated_cue_regions(located, cue, other_cues=CUES.values())]
        expanded = layout.associated(regions, cue, norm.parse_date,
                                     other_cues=[*DATE_CUES.values(), *CUES.values()])
        for located in expanded:
            _, line = located
            match = cue.search(line.text)
            if not match:
                continue
            tail = line.text[match.end():]
            ends = [found.start() for pattern in DATE_CUES.values()
                    if (found := pattern.search(tail))]
            marking = match[0] + " " + (tail[:min(ends)] if ends else tail)
            parsed = norm.parse_date(marking)
            if parsed is None and not getattr(line, "_text_range", None) and any(layout.source(*located) in layout.sources(*other)
                                      and other[1] is not line and norm.parse_date(other[1].text)
                                      for other in expanded):
                continue
            item = _observation(located, parsed, lines, role=role)
            printed = date_candidates(marking)
            if printed:
                item["date_interpretations"] = printed["candidates"]
                if parsed and len(printed["candidates"]) == 1:
                    item["value"] = {**parsed, "day": printed["candidates"][0].get("day")}
                if printed["status"] == "needs_review":
                    item["status"] = "needs_review"
            output.append(item)
    # Keep less common/Hindi cues supported by the primary grammar.
    for item in _primary_readings(lines, CUES[DC.DATE_OF_PACKING], norm.parse_date, named_only=True):
        if not any(cue.search(item["raw"]) for name, cue in DATE_CUES.items()
                   if name in {"manufacturing_date", "packing_date"}):
            item["role"] = "unspecified_date"
            output.append(item)
    return output


def _reconcile_dates(declaration, lines):
    if declaration is None:
        return
    observations = _date_readings(lines)
    if not observations:
        return
    events = {}
    for role in ("manufacturing_date", "packing_date", "unspecified_date"):
        records = _deduplicate([item for item in observations if item["role"] == role])
        if not records:
            continue
        resolved = [item for item in records if item["value"]]
        months = {(item["value"]["year"], item["value"]["month"]) for item in resolved}
        days = {item["value"].get("day") for item in resolved if item["value"].get("day")}
        # Only a reading that produced a date can contradict another one. A cue
        # with nothing after it is a gap, and this pack carries two of them --
        # the words "MFG. DATE" inside a sentence asking the customer to quote
        # it, and a 0.02-confidence "MFD" fragment. Counting either as a
        # competing reading withheld a packing date that was read at 0.98.
        uncertain = (len(months) > 1 or len(days) > 1
                     or any(item["status"] == "needs_review" for item in resolved))
        values = sorted(resolved, key=lambda item: (item["raw"], str(item["sources"])))
        events[role] = {"status": "needs_review" if uncertain else "detected" if resolved else "cue_only",
                        "value": values[0]["value"] if resolved and not uncertain else None,
                        "observations": records}
    # The current primary class includes both events. Prefer an actual
    # manufacture event for its declaration rule, not whichever image came
    # first -- but only among events that were actually read. An event whose
    # cue appeared with no legible date behind it decides nothing, and letting
    # it outrank a clean reading is how a pack that plainly prints "PKD ON:
    # 01-JULY-26" ended up with no usable date at all.
    order = ("manufacturing_date", "packing_date", "unspecified_date")
    role = next((name for name in order
                 if events.get(name, {}).get("status") != "cue_only" and name in events),
                next(name for name in order if name in events))
    chosen = events[role]
    original_metadata = {key: value for key, value in declaration.norm.items()
                         if key.startswith("extraction_") or key == "source_spans"}
    declaration.norm = {**(chosen["value"] or {}), **original_metadata}
    _reconcile(declaration, chosen["observations"],
        lambda value: (value["year"], value["month"]), clear=("year", "month", "iso", "day"),
        reason="The same date event has uncertain or differing readings. Verify its printed role and value.")
    if chosen["status"] == "needs_review":
        declaration.norm.update(requires_review=True, year=None, month=None, iso=None, day=None,
            review_reason="The same date event has uncertain or differing readings. Verify its printed role and value.")
        declaration.norm["candidates"] = [
            {**(item["value"] or {}), "raw": item["raw"], "sources": item["sources"],
             "date_interpretations": item.get("date_interpretations", []),
             "ocr_alternatives": item["ocr_alternatives"]}
            | ({"text_range": item["text_range"], "region_text": item["region_text"]}
               if "text_range" in item else {})
            for item in chosen["observations"]]
    elif chosen["value"]:
        representative = min((item for item in chosen["observations"] if item["value"]),
                             key=lambda item: (item["raw"], str(item["sources"])))
        declaration.raw = representative["raw"]
        anchor = representative["sources"][-1]
        declaration.frame, declaration.bbox = anchor["frame"], tuple(anchor["bbox"])
        declaration.panel = Panel(anchor["panel"])
        declaration.scripts = norm.detect_scripts(declaration.raw)
    declaration.norm.update(date_role=role, date_events=events)
    # Date-driven applicability uses packing when explicitly printed; it must
    # not borrow manufacture's value when the packing event is disputed.
    event_role = ("packing_date"
                  if events.get("packing_date", {}).get("status", "cue_only") != "cue_only"
                  else role)
    declaration.norm["legal_date_role"] = event_role
    declaration.norm["date_evidence_uncertain"] = events[event_role]["status"] == "needs_review"
    attach(declaration, [s for item in chosen["observations"] for s in item["sources"]],
           method=get_provenance(declaration).method)


def _reconcile_contacts(declaration, lines, klass):
    if declaration is None:
        return
    observations = []
    for panel, anchor in _contact_anchors(lines, klass):
        role = ("importer" if re.search(r"\bimport", anchor.text, re.IGNORECASE)
                else "packer" if re.search(r"\bpack", anchor.text, re.IGNORECASE)
                else "manufacturer") if klass is DC.MANUFACTURER else "consumer_care"
        block = _block_after(lines, anchor, span=4)
        value = norm.parse_contact(block)
        if klass is DC.MANUFACTURER:
            value["name"] = anchor.text
        located = copy.copy(anchor)
        located.text = block
        located._extraction_method = "contact_block"
        # The lines this contact block actually rests on: whichever carry the
        # name, e-mail, telephone or PIN that were parsed out of it.
        decisive_text = [anchor.text, *(str(value[key]) for key in ("name", "email", "phone", "pin")
                                        if value.get(key))]
        observations.append(_observation((panel, located), value, lines, role=role,
                                         decisive_text=decisive_text))
    observations = _deduplicate(observations)
    if not observations:
        return
    preferred = next(role for role in ("manufacturer", "packer", "importer", "consumer_care")
                     if any(item["role"] == role for item in observations))
    complete = max((item for item in observations if item["role"] == preferred), key=lambda item: (
        sum(item["value"].get("has_" + key) is True for key in ("name", "address", "phone", "email")),
        item["ocr_confidence"] or 0, item["raw"]))
    # Choose a complete local block, never assemble contactability by taking an
    # address from one image and an unrelated phone/name from another.
    declaration.norm = dict(complete["value"])
    declaration.raw = complete["raw"]
    source = complete["sources"][0]
    declaration.frame, declaration.bbox, declaration.panel = source["frame"], tuple(source["bbox"]), Panel(source["panel"])
    conflict = False
    for role in {item["role"] for item in observations}:
        records = [item for item in observations if item["role"] == role]
        for key in ("name", "email", "phone", "pin"):
            values = {re.sub(r"\s+", " ", str(item["value"][key])).strip().casefold()
                      for item in records if item["value"].get(key)}
            conflict |= len(values) > 1
    _reconcile(declaration, observations,
        lambda value: str(sorted(value.items())) if conflict else "compatible_local_blocks",
        clear=("name", "phone", "email", "pin", "has_name", "has_phone", "has_email", "has_pin", "has_address"),
        reason="Different or uncertain readings within the same contact role require review. Separate manufacturer, packer and importer roles have not been combined into one party.")


def _reconcile_names(declarations, lines):
    surfaces = []
    for located in lines:
        if not any(layout.same_surface(located, surface[0]) for surface in surfaces):
            surfaces.append([item for item in lines if layout.same_surface(located, item)])
    for klass in (DC.GENERIC_NAME, DC.BRAND):
        declaration = declarations.get(klass)
        if declaration is None:
            continue
        observations = []
        for surface in surfaces:
            generic = _find_generic(surface)
            found = generic[1] if generic and klass is DC.GENERIC_NAME else (
                _guess_brand(surface, generic[0] if generic else None) if klass is DC.BRAND else None)
            if found is None:
                continue
            if klass is DC.BRAND and not getattr(found[1], "_identity_support", None):
                # Unsupported close-up fragments remain OCR/review evidence;
                # they cannot contradict an independently supported front name.
                continue
            if klass is DC.GENERIC_NAME and found[1].text.rstrip().endswith(("*", "†", "‡")):
                continue
            value = {"name": generic[0] if klass is DC.GENERIC_NAME
                     else getattr(found[1], "_brand_name", found[1].text).strip().lower()}
            observations.append(_observation(found, value, lines))
        if not observations:
            continue
        # A shorter commodity noun can repeat a fuller phrase (tea/green tea).
        # Do not join unrelated brand fragments or infer company/sub-brand roles.
        generic_names = [set(item["value"]["name"].split()) for item in observations]
        compatible = klass is DC.GENERIC_NAME and all(
            first <= second or second <= first for first in generic_names for second in generic_names)
        _reconcile(declaration, _deduplicate(observations),
            lambda value, compatible=compatible: "compatible_commodity" if compatible else "".join(
                character for character in value["name"].casefold() if character.isalnum()),
            clear=("name",), reason=f"Different or uncertain supported {klass.value.replace('_', ' ')} readings require review; no name has been chosen by image order.")


def _declaration(
    klass: DC,
    found: tuple[Panel, OcrLine] | None,
    parsed: dict | None,
    *,
    scripts: list[Script],
    fallback_raw: str = "",
) -> Declaration | None:
    if parsed is None and found is None:
        return None
    panel, line = found if found else (Panel.UNKNOWN, None)
    normalized = dict(parsed or {})
    declaration = Declaration(
        klass=klass,
        raw=(line.text if line else fallback_raw),
        norm=normalized,
        bbox=line.bbox if line else None,
        frame=line.frame if line else None,
        panel=panel,
        scripts=scripts,
        confidence=line.confidence if line else 0.0,
        path=ExtractionPath.CLASSICAL,
    )
    if line is not None and (getattr(line, "_low_confidence", False)
                             or getattr(line, "review_required", False)):
        declaration.norm.setdefault(
            "review_reason",
            "The recogniser was not confident of this text. It is retained as a "
            "review candidate and cannot on its own establish a finding; confirm "
            "it against the original image.")
        declaration.norm["requires_review"] = True
    return attach(declaration, layout.sources(panel, line) if line else [],
                  method=getattr(line, "_extraction_method", "keyword_pattern"))


def extract(results: list[tuple[Panel, OcrResult]], *, allergen_concerns=()) -> Extraction:
    """Build the declaration set from one or more captured panels."""

    lines: list[tuple[Panel, OcrLine]] = []
    spans: list[TextSpan] = []
    for capture_index, (panel, result) in enumerate(results):
        for original in result.lines:
            line = copy.copy(original)
            line._capture_index = capture_index
            line._image_size = (result.width, result.height)
            line._identity_degraded = any(issue.get("code") == "blur_or_low_detail"
                                          for issue in result.quality.get("issues", []))
            lines.append((panel, line))
            spans.append(
                TextSpan(
                    text=line.text,
                    bbox=line.bbox,
                    confidence=line.confidence,
                    panel=panel,
                    script=norm.dominant_script(line.text),
                    height_px=line.height_px,
                    frame=line.frame,
                )
            )

    # A label welded to its value by a printed arrow is two detections sharing a
    # row, not one declaration. Split before any cue matching, so a label cannot
    # claim the value the arrow happens to land on.
    lines = layout.split_pointer_lines(lines)

    out = Extraction(spans=spans)
    out.intelligence = extract_intelligence(lines, allergen_concerns=allergen_concerns)
    # Pixel-backed currency/amount hypotheses remain review observations even
    # when their OCR line is withheld below. Never use them in primary prices.
    price_reviews = layout.price_review_candidates(lines)
    if price_reviews:
        recorded_prices = out.intelligence.setdefault("fields", {}).setdefault("retail_sale_price", [])
        for candidate in price_reviews:
            if candidate not in recorded_prices:
                recorded_prices.append(candidate)
    conflicts = [line for _, line in lines if getattr(line, "review_required", False)]
    if conflicts:
        out.warnings.append(
            f"{len(conflicts)} text region(s) have uncertain or conflicting OCR readings. "
            "Verify the original marking or capture a close-up; no missing-declaration conclusion is justified."
        )
    out.lines_read = len(lines)
    out.legible_lines = sum(
        1 for _, l in lines if l.confidence >= LEGIBLE_CONFIDENCE and l.text.strip()
    )
    out.mean_confidence = (
        sum(l.confidence for _, l in lines) / len(lines) if lines else 0.0
    )
    all_lines = list(lines)
    # Low confidence is not absence. Deleting sub-threshold lines outright
    # removed the declarations from exactly the packages that most need
    # screening: variable data is inkjet-coded onto foil and reads at 0.4-0.55,
    # while the offset-printed nutrition table beside it reads at 0.9. On the
    # Nakoda pack the real "400g 20125M3s" scored 0.540 against this 0.55 floor
    # and vanished, taking its "NET QUANTITY" label with it, so the most
    # prominent quantity left in the capture was a nutrition cell.
    #
    # Admit them instead and mark them, so the found-versus-trusted split -- not
    # a delete -- decides what may become a finding. `_declaration` turns the
    # flag into `requires_review`, and `_best_line` still prefers a confident
    # line whenever one exists, so nothing that used to be trusted stops being.
    for _, line in lines:
        if line.confidence < LEGIBLE_CONFIDENCE:
            line._low_confidence = True
    # Recogniser *disagreement* is excluded whatever it scores: putting one of
    # two conflicting numbers in front of an officer anchors them worse than
    # showing none, and the conflicting readings are retained in intelligence.
    lines = [(p, l) for p, l in lines
             if l.text.strip()
             and (l.confidence >= LEGIBLE_CONFIDENCE
                  or (l.confidence >= CANDIDATE_CONFIDENCE
                      and (not getattr(l, "review_required", False)
                           or _alternatives_agree(l))))]
    if not lines:
        out.warnings.append("no text was recognised on any captured panel")
        return out
    if out.legible_lines < 3:
        out.warnings.append(
            f"Only {out.legible_lines} line(s) of text were legible. Absence of a "
            "declaration cannot be established from a capture this degraded; the "
            "presence checks will report as inconclusive."
        )

    full_text = "\n".join(line.text for _, line in lines)
    out.full_text = full_text
    decls: dict[DC, Declaration] = {}

    # ---- unit sale price first, so its rupee figure is not mistaken for MRP --
    up_test = lambda t: norm.parse_unit_price(t) is not None
    up_lines = layout.associated(lines, CUES[DC.UNIT_SALE_PRICE], norm.parse_unit_price,
                                 other_cues=list(CUES.values()))
    up_found = _best_line(up_lines, CUES[DC.UNIT_SALE_PRICE], up_test)
    up_parsed = norm.parse_unit_price(up_found[1].text) if up_found else None
    if up_parsed:
        decls[DC.UNIT_SALE_PRICE] = _declaration(
            DC.UNIT_SALE_PRICE, up_found, up_parsed,
            scripts=_scripts_for(lines, lambda t: CUES[DC.UNIT_SALE_PRICE].search(t) or up_test(t)),
            fallback_raw=up_parsed.get("raw", ""),
        )

    # ---- net quantity ----
    nq_test = lambda t: norm.parse_net_quantity(t) is not None
    quantity_lines = _quantity_pool(lines)
    nq_lines = layout.associated(quantity_lines, CUES[DC.NET_QUANTITY], norm.parse_net_quantity,
                                 other_cues=list(CUES.values()))
    nq_found = _best_line(nq_lines, CUES[DC.NET_QUANTITY], nq_test)
    nq_parsed = norm.parse_net_quantity(nq_found[1].text) if nq_found else None
    if nq_parsed or nq_found:
        decls[DC.NET_QUANTITY] = _declaration(
            DC.NET_QUANTITY, nq_found, nq_parsed,
            scripts=_scripts_for(
                quantity_lines, lambda t: bool(CUES[DC.NET_QUANTITY].search(t)) or nq_test(t)
            ),
        )

    # ---- retail sale price ----
    exclude = up_parsed.get("raw") if up_parsed else None
    price_test = lambda t: norm.parse_price(t, exclude=exclude) is not None
    mrp_lines = layout.associated(lines, CUES[DC.RETAIL_SALE_PRICE],
                                  lambda t: norm.parse_price(t, exclude=exclude),
                                  other_cues=list(CUES.values()))
    mrp_found = _best_line(mrp_lines, CUES[DC.RETAIL_SALE_PRICE], price_test)
    # count distinct prices across the whole label, not just the chosen line --
    # dual pricing is only visible in aggregate
    # Aggregate prices already parsed in local blocks. A whole-document regex
    # can otherwise weld a bare MRP cue to an unrelated number on another face.
    local_prices = [norm.parse_price(line.text, exclude=exclude) for _, line in mrp_lines]
    local_prices = [price for price in local_prices if price]
    mrp_parsed = dict(local_prices[0]) if local_prices else None
    if mrp_parsed:
        distinct = sorted({v for price in local_prices for v in price["distinct_values"]})
        mrp_parsed.update(distinct_values=distinct, count=len(distinct))
    if mrp_parsed and mrp_found:
        line_parsed = norm.parse_price(mrp_found[1].text, exclude=exclude)
        if line_parsed:
            mrp_parsed["value"] = line_parsed["value"]
        mrp_parsed["has_tax_clause"] = any(
            p in full_text.lower() for p in norm._TAX_PHRASES
        )
    if mrp_parsed or mrp_found:
        # "inclusive of all taxes" is very often set on its own line under the
        # price, so the raw text tested against Rule 6(1)(e) has to be the whole
        # price block, not just the line carrying the number.
        block: list[str] = []
        price_sources = [source for panel, line in mrp_lines if price_test(line.text)
                         for source in layout.sources(panel, line)]
        if mrp_found:
            block.append(mrp_found[1].text)
            price_sources.extend(layout.sources(*mrp_found))
        for panel, line in lines:
            if line.text in block:
                continue
            if (
                CUES[DC.RETAIL_SALE_PRICE].search(line.text)
                or "tax" in line.text.lower()
                or "कर" in line.text
            ):
                block.append(line.text)
                price_sources.extend(layout.sources(panel, line))

        decl = _declaration(
            DC.RETAIL_SALE_PRICE, mrp_found, mrp_parsed,
            scripts=_scripts_for(
                lines, lambda t: bool(CUES[DC.RETAIL_SALE_PRICE].search(t)) or price_test(t)
            ),
        )
        if decl is not None:
            decl.raw = "\n".join(block).strip()
            attach(decl, price_sources, method=get_provenance(decl).method)
            decls[DC.RETAIL_SALE_PRICE] = decl

    # ---- date of packing ----
    date_test = lambda t: norm.parse_date(t) is not None
    date_associations = layout.associated(lines, CUES[DC.DATE_OF_PACKING], norm.parse_date,
                                          other_cues=list(CUES.values()))
    date_lines = [(p, l) for p, l in date_associations if CUES[DC.DATE_OF_PACKING].search(l.text)]
    dt_found = _best_line(date_lines, CUES[DC.DATE_OF_PACKING], date_test)
    dt_parsed = norm.parse_date(dt_found[1].text) if dt_found else None
    if dt_parsed or dt_found:
        decls[DC.DATE_OF_PACKING] = _declaration(
            DC.DATE_OF_PACKING, dt_found, dt_parsed,
            scripts=_scripts_for(
                lines, lambda t: bool(CUES[DC.DATE_OF_PACKING].search(t)) or date_test(t)
            ),
        )

    # ---- manufacturer / packer ----
    # Located on the unfiltered lines. A heading recognised at 0.54 is still a
    # heading: withholding it does not make the declaration uncertain, it makes
    # the declaration disappear, and the rule then reports a pack that names its
    # manufacturer in bold as one whose manufacturer could not be established.
    # Whether the block may found a finding is decided by review, below.
    mfr_lines = _contact_anchors(all_lines, DC.MANUFACTURER)
    if mfr_lines:
        panel, anchor = mfr_lines[0]
        block = _block_after(all_lines, anchor, span=4)
        parsed = norm.parse_contact(block)
        parsed["name"] = anchor.text
        decls[DC.MANUFACTURER] = Declaration(
            klass=DC.MANUFACTURER, raw=block, norm=parsed, bbox=anchor.bbox,
            frame=anchor.frame,
            panel=panel, scripts=norm.detect_scripts(block), confidence=anchor.confidence,
        )

    # ---- consumer care ----
    care_lines = _contact_anchors(all_lines, DC.CONSUMER_CARE)
    if care_lines:
        panel, anchor = care_lines[0]
        block = _block_after(all_lines, anchor, span=4)
        parsed = norm.parse_contact(block)
        decls[DC.CONSUMER_CARE] = Declaration(
            klass=DC.CONSUMER_CARE, raw=block, norm=parsed, bbox=anchor.bbox,
            frame=anchor.frame,
            panel=panel, scripts=norm.detect_scripts(block), confidence=anchor.confidence,
        )

    # ---- generic name and brand ----
    generic_found = _find_generic(lines)
    generic_hit = generic_found[0] if generic_found else None
    if generic_found:
        generic_hit, found = generic_found
        generic_norm = {"name": generic_hit, "matched_lexicon": True}
        if found[1].text.rstrip().endswith(("*", "†", "‡")):
            generic_norm.update(name=None, requires_review=True, candidates=[{"name": generic_hit}],
                review_reason="The commodity-like wording has a footnote marker; verify whether it names the product or a flavour/claim.")
        decls[DC.GENERIC_NAME] = _declaration(
            DC.GENERIC_NAME, found, generic_norm,
            scripts=norm.detect_scripts(found[1].text if found else generic_hit),
            fallback_raw=generic_hit,
        )
        if decls[DC.GENERIC_NAME]:
            attach(decls[DC.GENERIC_NAME], layout.sources(*found), method="generic_name_lexicon")
    else:
        prose = _generic_in_prose(lines)
        if prose:
            out.warnings.append(
                f"The word “{prose}” appears on the label, but only inside "
                "descriptive copy rather than as a declaration of what the "
                "commodity is. Rule 6(1)(b) is reported as undetermined rather "
                "than satisfied by it."
            )

    brand = _guess_brand(lines, generic_hit)
    if brand is not None:
        panel, line = brand
        brand_norm = {"name": getattr(line, "_brand_name", line.text).strip().lower()}
        if support := getattr(line, "_identity_support", None):
            brand_norm["identity_support"] = support
        if reason := getattr(line, "_identity_review_reason", None):
            brand_norm.update(name=None, requires_review=True, review_reason=reason,
                              candidates=[{"name": brand_norm["name"]}])
            out.warnings.append(reason)
        decls[DC.BRAND] = Declaration(
            klass=DC.BRAND, raw=line.text, norm=brand_norm,
            bbox=line.bbox, panel=panel, scripts=norm.detect_scripts(line.text),
            frame=line.frame,
            confidence=line.confidence,
        )
        attach(decls[DC.BRAND], layout.sources(panel, line), method=getattr(line, "_extraction_method", "brand_layout_heuristic"))

    # ---- country of origin ----
    origin_lines = layout.associated(lines, CUES[DC.COUNTRY_OF_ORIGIN], norm.parse_country_of_origin,
                                     other_cues=list(CUES.values()))
    origin_lines = [(p, line) for p, line in origin_lines if CUES[DC.COUNTRY_OF_ORIGIN].search(line.text)]
    found = _best_line(origin_lines, CUES[DC.COUNTRY_OF_ORIGIN], norm.parse_country_of_origin)
    origin = norm.parse_country_of_origin(found[1].text) if found else None
    if origin or found:
        decls[DC.COUNTRY_OF_ORIGIN] = _declaration(
            DC.COUNTRY_OF_ORIGIN, found, origin,
            scripts=norm.detect_scripts(found[1].text),
        )

    out.declarations = {k: v for k, v in decls.items() if v is not None}
    # Multiple neighboring values around one bare cue are an association
    # ambiguity, not proof that the package printed two MRPs. Preserve each
    # reading for review and withhold uncertain arithmetic inputs.
    for klass, located_lines, parser in [
        (DC.RETAIL_SALE_PRICE, mrp_lines, norm.parse_price),
        (DC.NET_QUANTITY, nq_lines, norm.parse_net_quantity),
        (DC.UNIT_SALE_PRICE, up_lines, norm.parse_unit_price),
    ]:
        groups: dict[tuple, list[dict]] = {}
        group_sources: dict[tuple, list[dict]] = {}
        for panel, line in located_lines:
            spans_for_line = getattr(line, "_source_spans", None)
            if not spans_for_line or len(spans_for_line) < 2:
                continue
            anchor = spans_for_line[0]
            key = (anchor.get("image_index"), anchor.get("frame"), tuple(anchor["bbox"]), anchor["text"])
            parsed = parser(line.text)
            if parsed:
                groups.setdefault(key, []).append(parsed)
                group_sources.setdefault(key, []).extend(layout.sources(panel, line))
        candidates = [value for values in groups.values()
                      if len({(v.get("value"), v.get("unit"), v.get("per_unit")) for v in values}) > 1
                      for value in values]
        declaration = out.declarations.get(klass)
        if candidates and declaration:
            declaration.norm.update(requires_review=True, candidates=candidates)
            for key in ("value", "value_base", "count", "distinct_values"):
                if key in declaration.norm:
                    declaration.norm[key] = None
            out.warnings.append(
                f"The {klass.value.replace('_', ' ')} cue has more than one plausible neighboring value. "
                "Verify the source image before using it for a finding."
            )
            candidate_sources = [source for key, values in groups.items()
                                 if len({(v.get("value"), v.get("unit"), v.get("per_unit")) for v in values}) > 1
                                 for source in group_sources[key]]
            provenance = get_provenance(declaration)
            attach(declaration, [*provenance.sources, *candidate_sources], method=provenance.method)
    for klass, declaration in out.declarations.items():
        if declaration.provenance is None:
            source_line = next(((p, l) for p, l in lines if l.frame == declaration.frame and l.bbox == declaration.bbox), None)
            method = "contact_block" if klass in {DC.MANUFACTURER, DC.CONSUMER_CARE} else "brand_layout_heuristic" if klass is DC.BRAND else "keyword_pattern"
            attach(declaration, layout.sources(*source_line) if source_line else [], method=method)
    # Reconciliation re-derives its pool, so it has to re-derive the cue/value
    # associations with it. Without them a label split from its value by a
    # printed arrow has no "named" reading at all, and `named_only` below then
    # discards the correct value that `_best_line` already found.
    quantity_pool = layout.associated(
        _quantity_pool(all_lines), CUES[DC.NET_QUANTITY], norm.parse_net_quantity,
        other_cues=list(CUES.values()))
    has_quantity_cue = any(CUES[DC.NET_QUANTITY].search(line.text) for _, line in quantity_pool)
    # The mandatory declarations are printed together. A face that names the net
    # quantity carries its value too, so once a cue has been seen, quantities on
    # the other captures are not candidates for it -- which is what stopped a
    # nutrition cell on the back panel being read as the pack weight.
    if has_quantity_cue:
        cued_captures = {getattr(line, "_capture_index", None)
                         for _, line in quantity_pool
                         if CUES[DC.NET_QUANTITY].search(line.text)}
        quantity_pool = [(p, line) for p, line in quantity_pool
                         if getattr(line, "_capture_index", None) in cued_captures]
    for klass, pool, parser, signature, clear, named_only in [
        (DC.NET_QUANTITY, quantity_pool, norm.parse_net_quantity,
         lambda value: _semantic_value("net_quantity", value),
         ("value", "value_base", "unit", "unit_base", "unit_raw", "is_canonical"), has_quantity_cue),
        (DC.UNIT_SALE_PRICE, all_lines, norm.parse_unit_price,
         lambda value: _semantic_value("unit_sale_price", value),
         ("value", "per_value", "per_base", "per_unit", "unit_base"), False),
        (DC.RETAIL_SALE_PRICE, all_lines, norm.parse_price,
         lambda value: (value.get("currency"), tuple(value.get("distinct_values", []))),
         ("value",), False),
        (DC.COUNTRY_OF_ORIGIN, all_lines, norm.parse_country_of_origin,
         lambda value: value.get("country", "").casefold(), ("country",), True),
    ]:
        observations = _primary_readings(pool, CUES[klass], parser, named_only=named_only,
                                         split_repeated=klass is DC.NET_QUANTITY)
        _reconcile(out.declarations.get(klass), observations, signature, clear=clear,
            reason=f"Multiple or uncertain {klass.value.replace('_', ' ')} readings require source review; no single value has been selected for rules.")
    # A label that prints a net-quantity keyword has said where its net quantity
    # is. When nothing cued parses, the value still on the declaration came from
    # `_best_line`'s "most prominent number on the panel" fallback -- which on a
    # pack whose inkjet value column is printed out of register with its
    # pre-printed labels picks a nutrition cell ("Saturated Fat 17.22 g") as the
    # pack weight. Keep it as a review candidate instead of declaring it.
    net = out.declarations.get(DC.NET_QUANTITY)
    if (has_quantity_cue and net is not None and net.norm.get("value") is not None
            and not CUES[DC.NET_QUANTITY].search(net.raw or "")):
        corroboration = _price_corroborates_quantity(out.declarations, net.norm)
        if corroboration:
            # The label's own arithmetic settles which number is the quantity,
            # which is what a person does when the arrows are out of register.
            # It also answers the separate hold placed on the same reading for
            # being faint: an inkjet-coded "400g" recognised at 0.54 and an
            # independent MRP-over-unit-price calculation that lands on the same
            # figure are two sources, and together they are not a guess.
            net.norm["corroborated_by"] = corroboration
            if net.norm.get("value") is not None:
                net.norm["requires_review"] = False
                net.norm.pop("review_reason", None)
        else:
            net.norm.update(
                requires_review=True,
                review_reason=(
                    "A net-quantity keyword is printed on the label but no value could be "
                    "tied to it -- on this kind of pack the arrow from the label points at "
                    "the neighbouring field's value. The quantity offered is the most "
                    "prominent one on the declaring face; confirm it against the original."),
                value=None, value_base=None, unit=None, unit_base=None, is_canonical=False)
    _reconcile_dates(out.declarations.get(DC.DATE_OF_PACKING), all_lines)
    for klass in (DC.MANUFACTURER, DC.CONSUMER_CARE):
        _reconcile_contacts(out.declarations.get(klass), all_lines, klass)
    _reconcile_names(out.declarations, all_lines)
    for declaration in out.declarations.values():
        reason = declaration.norm.get("review_reason")
        if declaration.norm.get("requires_review") and reason and reason not in out.warnings:
            out.warnings.append(reason)
    out.intelligence["declarations"] = {
        klass.value: intelligence_record(declaration)
        for klass, declaration in out.declarations.items()
    }
    out.is_imported = norm.looks_imported(full_text)
    # Per line, with intra-line spaces closed up: a printed EAN is routinely set
    # as "8 901719 134845". Scanning line by line keeps two unrelated numbers on
    # separate lines from ever being welded into one plausible-looking GTIN.
    out.gtin_candidates = [
        m.group(1)
        for _, line in lines
        for m in GTIN_RE.finditer(line.text.replace(" ", ""))
    ]
    return out


def _contact_anchors(lines, klass):
    """Lines that introduce a contact block, including headings split in two.

    A cue that matches an original line anchors there. A cue that only appears
    once two consecutive lines are read together anchors on the lower of them,
    which is where the block's details continue from.
    """
    direct = [(panel, line) for panel, line in lines if CUES[klass].search(line.text)]
    if direct:
        return direct
    return [getattr(line, "_bridge_anchor", (panel, line))
            for panel, line in layout.bridged(lines) if CUES[klass].search(line.text)]


def _block_after(
    lines: list[tuple[Panel, OcrLine]], anchor: OcrLine, span: int = 3
) -> str:
    """A geometrically adjacent block on one image, bounded by other headings."""
    index = next((i for i, (_, line) in enumerate(lines) if line is anchor), None)
    if index is None:
        return anchor.text
    from .intelligence import HEADING
    # An address continues below its heading, never beside it.
    block = layout.text_block(lines, lines[index], stop=HEADING, limit=span, below_only=True)
    anchor._source_spans = [layout.source(p, line) for p, line in block]
    return "\n".join(line.text for _, line in block)


def _generic_qualifies(text: str, preceding: str = "") -> bool:
    """Is this a declaration of the commodity, or copy that merely mentions it?

    `preceding` is the line above. Where a recogniser breaks a line is an
    artefact of the layout, not of the language: "MADE WITH" and "QUALITY
    SPICES" arrive as two separate lines, and judging the second on its own
    reads a marketing claim as a declaration of what the commodity is. The
    phrase has to be tested across the break.
    """
    if GENERIC_DISQUALIFIERS.search(text):
        return False
    if preceding and GENERIC_DISQUALIFIERS.search(f"{preceding} {text}"):
        return False
    return len(text.split()) <= GENERIC_MAX_WORDS


def _find_generic(
    lines: list[tuple[Panel, OcrLine]],
    *, readable_identity_only: bool = False,
) -> tuple[str, tuple[Panel, OcrLine]] | None:
    """Locate the Rule 6(1)(b) generic name, rejecting marketing prose.

    Searched line by line rather than over the concatenated label text, because
    the context a term sits in is exactly what decides whether it is a
    declaration. "Spices" standing alone on a masala packet is one; the same
    word inside "made with quality spices" on a noodle packet is not, and a
    whole-document substring search cannot tell them apart.

    The longest matching lexicon term wins -- "mustard oil" over "oil" -- and
    within a term the most prominent rendering wins.
    """
    for term in sorted(GENERIC_LEXICON, key=len, reverse=True):
        best: tuple[Panel, OcrLine] | None = None
        for panel, line in lines:
            if readable_identity_only and (not context.readable_identity_support((panel, line), LEGIBLE_CONFIDENCE)
                                           or norm.has_unreadable_glyphs(line.text)):
                continue
            text = line.text.strip()
            qualifier = GENERIC_DISQUALIFIERS.search(text) or context.FLAVOUR.search(text)
            if qualifier:
                text = text[:qualifier.start()].strip()
            if (not _term_pattern(term).search(text) or not _generic_qualifies(text)
                    or context.generic_context_blocked(lines, (panel, line), GENERIC_DISQUALIFIERS, text=text)):
                continue
            # The final commodity noun heads a single phrase: chocolate
            # biscuits, masala noodles, milk chocolate. Retain the full line.
            end = max(match.end() for match in _term_pattern(term).finditer(text))
            if any(match.end() > end for other in GENERIC_LEXICON
                   for match in _term_pattern(other).finditer(text)):
                continue
            if best is None or (line.height_px or 0) > (best[1].height_px or 0):
                best = (panel, line)
        if best is not None:
            return term, best
    return None


def _generic_in_prose(lines: list[tuple[Panel, OcrLine]]) -> str | None:
    """A commodity word that was seen, but only inside descriptive copy."""
    for term in sorted(GENERIC_LEXICON, key=len, reverse=True):
        for _, line in lines:
            if _term_pattern(term).search(line.text):
                return term
    return None


def _guess_brand(
    lines: list[tuple[Panel, OcrLine]], generic: str | None
) -> tuple[Panel, OcrLine] | None:
    """Rank plausible identity text and assemble nearby name fragments.

    Crude, and honestly labelled as such: it exists so Rule 6(1)(b) can test
    whether the generic name is merely the brand repeated. The filters below
    exist because without them the "brand" recorded against a pack tends to be
    whichever piece of shelf-shout was set largest -- "12.5% EXTRA", "₹10" --
    which then lands in the product repository as if it identified the product.
    """
    candidates = []
    generic_patterns = [_term_pattern(term) for term in GENERIC_LEXICON]
    for panel, line in lines:
        text = line.text.strip()
        explicit = context.BRAND_CUE.search(text)
        if explicit and 2 <= len(explicit[1]) <= 40 and sum(c.isalpha() for c in explicit[1]) >= 3:
            named = copy.copy(line)
            named._brand_name = explicit[1]
            named._extraction_method = "explicit_brand_cue"
            candidates.append((panel, named))
            continue
        if len(text) < 2 or len(text) > 40:
            continue
        if any(cue.search(text) for cue in CUES.values()):
            continue
        if norm.parse_net_quantity(text) or norm.parse_price(text):
            continue
        if generic and _term_pattern(generic).search(text):
            continue
        # a brand is a name: it needs letters, not just a number and a symbol
        if sum(c.isalpha() for c in text) < 3:
            continue
        # marketing copy is not a brand either
        if GENERIC_DISQUALIFIERS.search(text):
            continue
        if not context.brand_text_allowed(lines, (panel, line), generic_patterns=generic_patterns):
            continue
        # A clipped commodity token must not be promoted to brand identity.
        # Retain it in OCR without inventing the missing characters.
        tokens = re.findall(r"[a-z]+", text.casefold())
        if any(len(token) >= 4 and term != token and term.endswith(token) and 0 < len(term) - len(token) <= 2
               for token in tokens for term in GENERIC_LEXICON if " " not in term):
            continue
        # "12.5% EXTRA", "25% OFF", "₹10" -- shelf-shout, often the largest text
        # on the panel, and it used to end up in the repository as the product's
        # identity. A brand's first word carries a letter and claims no percentage.
        if "%" in text:
            continue
        first = text.split()[0] if text.split() else ""
        if not any(c.isalpha() for c in first):
            continue
        candidates.append((panel, line))
    if not candidates:
        return None
    explicit_candidates = [candidate for candidate in candidates if hasattr(candidate[1], "_brand_name")]
    # A global commodity match cannot validate a brand on another image. Cache
    # only within the exact panel/frame/capture identity used by layout joins.
    commodity_by_surface = {}
    support_by_candidate = {}
    for candidate in candidates:
        panel, line = candidate
        key = (panel, line.frame, getattr(line, "_capture_index", None))
        if key not in commodity_by_surface:
            same_image = [source for source in lines if layout.same_surface(candidate, source)]
            commodity_by_surface[key] = _find_generic(same_image, readable_identity_only=True)
        support_by_candidate[id(line)] = context.identity_support(candidate, commodity_by_surface[key])
    supported = [candidate for candidate in candidates if support_by_candidate[id(candidate[1])]]
    selected = max(explicit_candidates or supported or candidates,
                   key=lambda pl: (context.height(pl[1]) * pl[1].confidence, pl[1].width_px))
    brand = selected if explicit_candidates else context.join_brand(candidates, selected)
    support = support_by_candidate[id(selected[1])]
    if support:
        brand[1]._identity_support = support
    reason = None
    if getattr(brand[1], "review_required", False):
        reason = "Possible brand text includes uncertain OCR; verify the original identity marking before using it as product identity."
    elif getattr(selected[1], "_identity_degraded", False):
        reason = "Possible brand text comes from a blurred capture; verify a clear identity panel before using it as product identity."
    elif not support:
        reason = "Possible brand text has no readable commodity or explicit brand cue on the same source image; verify a complete identity panel before using it as product identity."
    if reason:
        brand[1]._identity_review_reason = reason
    return brand
