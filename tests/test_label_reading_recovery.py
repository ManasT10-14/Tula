"""Readings a real Indian pack makes that the pipeline used to throw away.

Every case here was found on one photographed packet of namkeen, and each on its
own was enough to make most of the rule set report "inconclusive" on a label a
person reads at a glance. They are kept together because they share a shape: the
recogniser did its job, and something downstream decided the result was too
uncertain to use.
"""
from __future__ import annotations

import pytest

from tula.extract import context, normalizers as norm
from tula.extract.intelligence import date_candidates


# --------------------------------------------------------------------------
# What is not a date
# --------------------------------------------------------------------------

def test_consumer_care_opening_hours_are_not_a_manufacture_date():
    """"BETWEEN 10.00 AM TO 6.00 PM" has the shape of a MM.YY date.

    It appears on a line that also says "MFG. DATE", because the sentence asks
    the customer to quote the batch number and manufacturing date when they
    write in. The cue window walked into the opening hours and reported a pack
    manufactured in October 2000, which then conflicted with the real printed
    date and withheld every rule gated on dated applicability.
    """
    text = ("BATCH NO. AND MFG. DATE) TO US AT ABOVE (ON WORKING DAYS "
            "BETWEEN 10.00 AM T0 6.00 PM)")
    assert norm.parse_date(text) is None


@pytest.mark.parametrize("text,iso", [
    ("PKD ON:01-JULY-26", "2026-07"),
    ("MFG 03/2026", "2026-03"),
])
def test_a_real_printed_date_still_reads(text, iso):
    assert norm.parse_date(text)["iso"] == iso


def test_two_digit_year_resolves_and_records_the_century_it_assumed():
    """The century is settled by the parser, so nothing is left open by it.

    Indian packaging prints "JULY-26", not "JULY-2026". Treating that as
    unresolved made the date declaration undecided on essentially every real
    pack. The assumption is kept on the record instead.
    """
    parsed = date_candidates("PKD ON:01-JULY-26")
    assert parsed["status"] == "detected"
    assert parsed["candidates"][0]["iso"] == "2026-07-01"
    assert "2000" in parsed["two_digit_year_assumption"]


def test_a_genuinely_ambiguous_date_is_still_unresolved():
    """08/09/26 is two dates, and no assumption settles which."""
    parsed = date_candidates("EXP 08/09/26")
    assert parsed["status"] == "needs_review"
    assert len(parsed["candidates"]) == 2


# --------------------------------------------------------------------------
# What is not a country of origin
# --------------------------------------------------------------------------

def test_allergen_sentence_is_not_an_origin_declaration():
    """"made in a facility that..." parsed as the country "A Facility That".

    That fictional second origin conflicted with the real "Product Of INDIA" on
    the same panel, so the declaration was withheld and the origin rule could
    not be reached.
    """
    assert norm.parse_country_of_origin(
        "This products is made in a facility that") is None


@pytest.mark.parametrize("text,country", [
    ("Product Of INDIA", "India"),
    ("Made in Germany", "Germany"),
    ("Country of Origin: Sri Lanka", "Sri Lanka"),
])
def test_a_real_origin_declaration_still_reads(text, country):
    assert norm.parse_country_of_origin(text)["country"] == country


# --------------------------------------------------------------------------
# The unit sale price an Indian pack actually prints
# --------------------------------------------------------------------------

def test_unit_price_after_a_rupee_slash_marker_without_its_own_symbol():
    """"MRP: 135/-0.34 perg" carries both prices and one currency mark.

    The unit price is set immediately after the MRP's "135/-" and has no symbol
    of its own, and the recogniser closes the gap in "per g". Requiring a
    currency symbol and a word boundary after "per" missed it entirely.
    """
    parsed = norm.parse_unit_price("MRP: 135/-0.34 perg")
    assert parsed["value"] == 0.34
    assert parsed["per_unit"] == "g" and parsed["per_value"] == 1.0


def test_reading_the_unit_price_does_not_consume_a_digit_of_the_mrp():
    """The caller removes the unit price's span before reading the MRP.

    An earlier version matched the "5/-" of "135/-" as its currency marker, so
    removing that span left "MRP: 13" and the pack was recorded at an MRP of 13.
    """
    line = "MRP: 135/-0.34 perg"
    unit = norm.parse_unit_price(line)
    assert not unit["raw"].startswith("5")
    assert norm.parse_price(line, exclude=unit["raw"])["value"] == 135.0


def test_a_nutrition_row_is_not_a_unit_price():
    """"per 100 g" with no price marker in front of it introduces nothing."""
    assert norm.parse_unit_price("Energy 564.48Kcal per 100 g") is None


# --------------------------------------------------------------------------
# Vocabulary against text whose spaces the recogniser dropped
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["SaturatedFat", "TransFat", "PolyunsaturatedFat"])
def test_a_glued_nutrition_row_is_still_recognised_as_one(text):
    """`\\b`-anchored terms cannot match inside a single token.

    "SaturatedFat" was admitted as a brand candidate, conflicted with the real
    front-of-pack name, and the identity was withheld.
    """
    assert context.mentions(context.NUTRITION, text)
    assert not context.NUTRITION.search(text), "the un-resegmented form is the bug"


def test_resegmentation_does_not_invent_a_match():
    assert not context.mentions(context.NUTRITION, "Ratlami Sev")
