"""GTIN forensics."""

from __future__ import annotations

import pytest

from tula.forensics import gtin


@pytest.mark.parametrize(
    "code,valid",
    [
        ("6901234567892", True),
        ("6901234567891", False),   # wrong check digit
        ("8901234567890", True),    # India prefix, check digit 0 is correct here
        ("8901234567891", False),
    ],
)
def test_check_digit(code, valid):
    assert gtin.check(code).check_digit_valid is valid


def test_check_digit_matches_gs1_algorithm():
    body = "690123456789"
    assert gtin.check_digit(body) == 2
    assert gtin.check(body + "2").valid


def test_prefix_resolves_to_allocating_organisation():
    assert gtin.check("6901234567892").allocated_to == "China"
    india_body = "890123456789"
    india = gtin.check(india_body + str(gtin.check_digit(india_body)))
    assert india.allocated_to == "India"
    assert india.valid


def test_restricted_prefixes_carry_no_origin_signal():
    body = "201234567890"
    result = gtin.check(body + str(gtin.check_digit(body)))
    assert result.is_restricted
    assert result.allocated_to is None


def test_origin_conflict_is_raised_but_hedged():
    result = gtin.check("6901234567892")
    message = gtin.origin_conflict(result, "India")
    assert message is not None
    # phrased as a prompt to verify, never as an assertion of an offence
    assert "not a violation in itself" in message


def test_no_conflict_when_prefix_and_declaration_agree():
    body = "890123456789"
    india = gtin.check(body + str(gtin.check_digit(body)))
    assert gtin.origin_conflict(india, "India") is None


def test_invalid_gtin_raises_no_origin_conflict():
    bad = gtin.check("6901234567891")
    assert gtin.origin_conflict(bad, "India") is None


def test_best_candidate_prefers_a_validating_number():
    picked = gtin.best_candidate(["12345678", "6901234567892"])
    assert picked.gtin == "6901234567892"
