"""Grammar tests.

The unit-symbol cases are the ones that matter most: "500 gms" is a real,
citable non-conformity that a presence-only pipeline can never see, so the
canonical/non-canonical split has to be exactly right.
"""

from __future__ import annotations

import pytest

from tula.domain.enums import Script
from tula.extract import normalizers as norm


@pytest.mark.parametrize(
    "symbol,expected_unit,canonical",
    [
        ("g", "g", True),
        ("kg", "kg", True),
        ("ml", "ml", True),
        ("mL", "ml", True),      # SI-legal; flagging it would be crying wolf
        ("gm", "g", False),
        ("gms", "g", False),
        ("Gms", "g", False),
        ("Kg", "kg", False),     # capital K is not the kilogram symbol
        ("KG", "kg", False),
        ("ML", "ml", False),
        ("Ltr", "l", False),
        ("litres", "l", False),
        ("pcs", "N", False),
        ("packet", "N", False),
    ],
)
def test_unit_symbol_conformance(symbol, expected_unit, canonical):
    result = norm.canonical_unit(symbol)
    assert result["unit"] == expected_unit
    assert result["is_canonical"] is canonical


def test_net_quantity_prefers_the_cued_value():
    # a nutrition panel is full of gram values that are not the net quantity
    text = "Energy 480 kcal\nProtein 6 g\nNet Wt. 200 g\nCarbohydrate 60 g"
    parsed = norm.parse_net_quantity(text)
    assert parsed["value"] == 200.0
    assert parsed["unit"] == "g"
    assert parsed["value_base"] == 200.0


def test_net_quantity_converts_to_base_units():
    assert norm.parse_net_quantity("Net Qty 1.5 kg")["value_base"] == 1500.0
    assert norm.parse_net_quantity("Net Vol 2 l")["value_base"] == 2000.0
    assert norm.parse_net_quantity("Net Wt 250 mg")["value_base"] == 0.25


def test_extra_free_is_flagged_not_swallowed():
    parsed = norm.parse_net_quantity("Net Wt. 90 g + 10 g free")
    assert parsed["value"] == 90.0
    assert parsed["has_extra_free"] is True


@pytest.mark.parametrize(
    "text,value",
    [
        ("MRP Rs. 45.00", 45.0),
        ("M.R.P ₹100", 100.0),
        ("Maximum Retail Price Rs 22.50", 22.5),
        ("MRP: 1,250.00 Rs", 1250.0),
    ],
)
def test_price_forms_collapse_to_one_number(text, value):
    assert norm.parse_price(text)["value"] == value


def test_dual_pricing_is_visible_in_the_count():
    parsed = norm.parse_price("MRP Rs. 45.00 ... revised MRP Rs. 55.00")
    assert parsed["count"] == 2
    assert parsed["distinct_values"] == [45.0, 55.0]


def test_tax_clause_detection():
    assert norm.parse_price("MRP Rs. 45.00 inclusive of all taxes")["has_tax_clause"]
    assert not norm.parse_price("MRP Rs. 45.00")["has_tax_clause"]


def test_unit_price_per_base_supports_the_arithmetic_audit():
    parsed = norm.parse_unit_price("Unit Sale Price: Rs. 22.50 per 100 g")
    assert parsed["value"] == 22.5
    assert parsed["per_base"] == 100.0

    per_kg = norm.parse_unit_price("Rs 225 per kg")
    assert per_kg["per_base"] == 1000.0


@pytest.mark.parametrize(
    "text,month,year",
    [
        ("Mfg: 03/2026", 3, 2026),
        ("Packed on MAR 2026", 3, 2026),
        ("Date of packing: 11-2025", 11, 2025),
        ("MFD Dec 2024", 12, 2024),
    ],
)
def test_date_parsing(text, month, year):
    parsed = norm.parse_date(text)
    assert (parsed["month"], parsed["year"]) == (month, year)


def test_script_detection_drives_rule_9_3():
    assert norm.detect_scripts("Net Wt. 200 g") == [Script.LATIN]
    assert norm.detect_scripts("शुद्ध वजन") == [Script.DEVANAGARI]
    both = norm.detect_scripts("शुद्ध वजन 200 g")
    assert Script.LATIN in both and Script.DEVANAGARI in both


def test_contact_needs_more_than_a_bare_number():
    rich = norm.parse_contact("Plot 42, MIDC Industrial Estate, Pune 411018")
    assert rich["has_address"] and rich["pin"] == "411018"

    bare = norm.parse_contact("Gold Foods")
    assert not bare["has_address"] and not bare["has_phone"]


def test_toll_free_and_mobile_numbers_both_parse():
    assert norm.parse_contact("Care: 1800 200 1234")["has_phone"]
    assert norm.parse_contact("Call 9876543210")["has_phone"]


def test_unreadable_script_is_reported_separately_from_absence():
    """A recogniser with no Devanagari emits placeholders for it.

    Reading that as "Hindi is absent" would convert a model limitation into a
    false accusation against a compliant bilingual pack.
    """
    placeholder = "\u25a1\u25a1\u25a1\u25a1\u25a1 \u25a1\u25a1\u25a1 200 \u25a1\u25a1\u25a1"
    assert norm.has_unreadable_glyphs(placeholder)
    scripts = norm.detect_scripts(placeholder)
    assert Script.UNREADABLE in scripts
    assert Script.DEVANAGARI not in scripts

    # clean text must never be flagged
    assert not norm.has_unreadable_glyphs("Net Wt. 200 g")
    assert Script.UNREADABLE not in norm.detect_scripts("शुद्ध वजन 200 g")
