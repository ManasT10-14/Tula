"""The four screens added alongside the original eighteen.

Each is deliberately narrow. Together they widen the pack past "is this
declaration present" into the *form* the declaration takes -- the dimension a
quantity is measured in, the currency a price is stated in, the components an
address carries -- and into one duty that a confirmed food package owes under
its own labelling regime rather than under these Rules.
"""
from __future__ import annotations

from datetime import date

import pytest

from tula.domain.enums import AssuranceTier, DeclarationClass as DC
from tula.domain.enums import CaptureCompleteness, Lane, Panel, Verdict
from tula.domain.models import Declaration, EvidenceCoverage, PackageFacts, Scan
from tula.rules.engine import RulesEngine

WHEN = date(2026, 7, 1)


@pytest.fixture(scope="module")
def engine():
    return RulesEngine.from_directory()


def scan():
    return Scan(scan_id="NEWRULES-QA", lane=Lane.FIELD, tier=AssuranceTier.C,
                coverage=EvidenceCoverage(
                    panels_captured=[Panel.PDP, Panel.BACK],
                    completeness=CaptureCompleteness.COMPLETE, attested_complete=True,
                    frames=2, lines_read=60, legible_lines=58, mean_confidence=0.93))


def package(**legal):
    base = {"category": "unknown", "bundle_type": "unknown", "shape": "unknown"}
    return PackageFacts(legal_context={**base, **legal})


def verdict(engine, rule_id, declarations, pkg=None):
    """The rule's verdict, or None when it did not produce a finding at all.

    A rule whose `applies_when` is definitely false drops out silently rather
    than recording NOT_APPLICABLE -- it had no subject to examine, and a record
    listing every rule that could not possibly apply is a record nobody reads.
    """
    findings = engine.evaluate_all(scan(), pkg or package(), declarations, {}, as_of=WHEN)
    return next((f.verdict for f in findings if f.rule_id == rule_id), None)


# --------------------------------------------------------------------------
# Rule 6(1)(c) with Rule 13 -- the dimension a quantity is measured in
# --------------------------------------------------------------------------

def quantity(**norm):
    return {DC.NET_QUANTITY: Declaration(
        klass=DC.NET_QUANTITY, raw="Net wt 400 g",
        norm={"value": 400.0, "unit": "g", "unit_base": "g", "value_base": 400.0,
              "is_canonical": True, "kind": "mass", **norm})}


@pytest.mark.parametrize("kind", ["mass", "volume", "length", "count"])
def test_a_recognised_dimension_passes(engine, kind):
    assert verdict(engine, "LMPCR.R6.1.C.DIMENSION", quantity(kind=kind)) is Verdict.PASS


def test_an_unrecognised_dimension_is_a_violation(engine):
    """A quantity in no recognised dimension is not a lawful declaration."""
    assert verdict(engine, "LMPCR.R6.1.C.DIMENSION",
                   quantity(kind="pieces-ish")) is Verdict.VIOLATION


def test_an_unread_dimension_is_undecided_rather_than_adverse(engine):
    """A gap in the reading is not a gap on the label."""
    assert verdict(engine, "LMPCR.R6.1.C.DIMENSION",
                   quantity(kind=None)) is Verdict.INCONCLUSIVE


def test_the_rule_drops_out_when_there_is_no_quantity_to_judge(engine):
    """No subject, no finding: the dimension rule has nothing to examine."""
    assert verdict(engine, "LMPCR.R6.1.C.DIMENSION", {}) is None


# --------------------------------------------------------------------------
# Rule 6(1)(e) -- the currency a price is stated in
# --------------------------------------------------------------------------

def price(**norm):
    return {DC.RETAIL_SALE_PRICE: Declaration(
        klass=DC.RETAIL_SALE_PRICE, raw="MRP Rs 135 (inclusive of all taxes)",
        norm={"value": 135.0, "currency": "INR", "count": 1,
              "has_tax_clause": True, **norm})}


def test_rupees_pass(engine):
    assert verdict(engine, "LMPCR.R6.1.E.CURRENCY", price()) is Verdict.PASS


def test_a_foreign_currency_is_a_violation(engine):
    assert verdict(engine, "LMPCR.R6.1.E.CURRENCY",
                   price(currency="USD")) is Verdict.VIOLATION


def test_an_unread_currency_is_undecided(engine):
    assert verdict(engine, "LMPCR.R6.1.E.CURRENCY",
                   price(currency=None)) is Verdict.INCONCLUSIVE


# --------------------------------------------------------------------------
# Rule 6(1)(a) with Rule 10 -- the components of the declared address
# --------------------------------------------------------------------------

def contact(**norm):
    return {DC.MANUFACTURER: Declaration(
        klass=DC.MANUFACTURER, raw="Manufactured by ACME FOODS PVT LTD, Indore 452001",
        norm={"has_name": True, "has_address": True, "has_pin": True, **norm})}


def test_a_block_with_name_locality_and_pin_passes(engine):
    assert verdict(engine, "LMPCR.R10.ADDRESS_COMPLETE", contact()) is Verdict.PASS


@pytest.mark.parametrize("missing", ["has_name", "has_address", "has_pin"])
def test_any_missing_component_is_a_violation(engine, missing):
    assert verdict(engine, "LMPCR.R10.ADDRESS_COMPLETE",
                   contact(**{missing: False})) is Verdict.VIOLATION


@pytest.mark.parametrize("unread", ["has_name", "has_address", "has_pin"])
def test_a_component_that_could_not_be_read_is_undecided(engine, unread):
    """None means unread. Only False means the label does not carry it."""
    assert verdict(engine, "LMPCR.R10.ADDRESS_COMPLETE",
                   contact(**{unread: None})) is Verdict.INCONCLUSIVE


# --------------------------------------------------------------------------
# Rule 6(1)(d) referral -- best-before on a confirmed food package
# --------------------------------------------------------------------------

def with_text(text):
    return package(category="food", category_confirmed=True,
                   confirmed_category="food", scope_evidence_text=text)


def test_a_confirmed_food_package_carrying_a_use_by_date_passes(engine):
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   with_text("use by: 30-nov-26")) is Verdict.PASS


def test_a_confirmed_food_package_with_no_such_date_is_a_violation(engine):
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   with_text("mrp 135 net quantity 400g")) is Verdict.VIOLATION


def test_it_drops_out_for_a_confirmed_non_food_package(engine):
    """A soap owes no best-before date, so the rule says nothing about it."""
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   package(category="cosmetic", category_confirmed=True,
                           confirmed_category="cosmetic",
                           scope_evidence_text="soap")) is None


def test_an_unconfirmed_category_leaves_it_unverified_rather_than_guessing(engine):
    """This is the whole point of gating it on a confirmed category.

    Whether a best-before duty exists at all is a food-law question, and a
    packaged-commodities screen that guessed the commodity was food would be
    inventing the duty it then enforces. Unverified sends the officer to the one
    fact that settles it, which is what the console's next-step panel reads.
    """
    # Both shapes an unsettled category takes: never chosen, and explicitly
    # left as "unknown" on a saved package-facts form.
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   package(scope_evidence_text="use by: 30-nov-26")) is Verdict.UNVERIFIED
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   package(category="unknown", category_confirmed=True,
                           scope_evidence_text="use by: 30-nov-26")) is Verdict.UNVERIFIED


def test_confirming_the_category_is_what_releases_it(engine):
    """The transition the demonstration turns on, asserted end to end."""
    text = "nakoda foods laung sev use by: 30-nov-26"
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   package(scope_evidence_text=text)) is Verdict.UNVERIFIED
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   with_text(text)) is Verdict.PASS


# --------------------------------------------------------------------------
# The pack as a whole
# --------------------------------------------------------------------------

def test_every_rule_carries_inspector_facing_text(engine):
    """A finding an officer cannot read is a finding nobody acts on."""
    for rule in engine.pack.rules:
        assert rule.plain, rule.id
        # The two withdrawn rules have nothing to do about them.
        if "Withdrawn" not in rule.plain:
            assert rule.plain_action, rule.id


def test_rule_identifiers_are_unique(engine):
    ids = [rule.id for rule in engine.pack.rules]
    assert len(ids) == len(set(ids))


def test_confirming_food_through_the_real_context_path_releases_the_rule(engine):
    """The demonstration moment, driven through the code the console runs.

    The tests above hand `confirmed_category` to the rule directly. This one
    never mentions it: it goes through `exemptions.apply`, which is what derives
    the key from the officer's answer, so a change there that stopped deriving
    it would fail here rather than silently making the rule undecidable again.
    """
    from tula.rules import exemptions

    text = "nakoda foods laung sev use by: 30-nov-26"
    declarations = {}

    def decide(legal):
        package = PackageFacts(legal_context={**legal, "scope_evidence_text": text})
        determination = exemptions.determine(
            declarations, raw_text=text, legal_context=package.legal_context,
            as_of=WHEN, policy=engine.pack.legal_policy)
        package = exemptions.apply(package, determination)
        findings = engine.evaluate_all(scan(), package, declarations, {}, as_of=WHEN)
        return next((f.verdict for f in findings
                     if f.rule_id == "LMPCR.R6.1.D.EXPIRY_FOOD"), None)

    assert decide({"category": "unknown"}) is Verdict.UNVERIFIED
    assert decide({"category": "food", "category_confirmed": True}) is Verdict.PASS
    assert decide({"category": "cosmetic", "category_confirmed": True}) is None
    # Confirmation without a choice is still no choice.
    assert decide({"category": "unknown", "category_confirmed": True}) is Verdict.UNVERIFIED


def test_a_partial_capture_cannot_convict_a_food_package_of_a_missing_date(engine):
    """Absence of evidence is not evidence of absence, for text as for fields.

    This rule first shipped asserting the phrase against the package-wide
    evidence text with `contains_phrase`, which returns a plain False when the
    wording is not there -- so a single-face capture of a food package produced
    a violation for a best-before date that may simply be on a face nobody
    photographed. `bench.py`'s `partial_capture` scenario caught it. The
    coverage-aware `declares_phrase` is what makes the miss undecided instead.
    """
    partial = Scan(scan_id="PARTIAL-QA", lane=Lane.FIELD, tier=AssuranceTier.C,
                   coverage=EvidenceCoverage(
                       panels_captured=[Panel.PDP],
                       completeness=CaptureCompleteness.PARTIAL,
                       frames=1, lines_read=30, legible_lines=28, mean_confidence=0.9))
    pkg = with_text("nakoda foods laung sev 400g mrp 135")
    findings = engine.evaluate_all(partial, pkg, {}, {}, as_of=WHEN)
    outcome = next(f.verdict for f in findings if f.rule_id == "LMPCR.R6.1.D.EXPIRY_FOOD")
    assert outcome is Verdict.INCONCLUSIVE

    # The same missing wording on a capture that *can* prove absence is a
    # violation, or the rule would never decide anything.
    assert verdict(engine, "LMPCR.R6.1.D.EXPIRY_FOOD", {},
                   with_text("nakoda foods laung sev 400g mrp 135")) is Verdict.VIOLATION
