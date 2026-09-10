"""Telling an officer which fact is holding which check up.

The console has always had a form for the package facts and no way to say what
any of them was for. The answer is derived by re-running the pack with the fact
assumed, rather than by listing beside each rule the facts its gates happen to
consult -- a second copy of the applicability logic that would go stale on its
own schedule.
"""
from __future__ import annotations

from datetime import date

import pytest

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import AssuranceTier, CaptureCompleteness, Lane, Panel, Verdict
from tula.domain.models import Declaration, EvidenceCoverage, PackageFacts, Scan
from tula.rules import unblock
from tula.rules.engine import RulesEngine

CONTEXT = {"category": "unknown", "bundle_type": "unknown", "shape": "unknown"}


@pytest.fixture(scope="module")
def engine():
    return RulesEngine.from_directory()


def scan():
    return Scan(scan_id="UNBLOCK-QA", lane=Lane.FIELD, tier=AssuranceTier.C,
                coverage=EvidenceCoverage(panels_captured=[Panel.PDP, Panel.BACK],
                                          frames=2, lines_read=40, legible_lines=38,
                                          mean_confidence=0.9))


def package(**legal):
    return PackageFacts(legal_context={**CONTEXT, **legal})


def declarations():
    return {
        DC.NET_QUANTITY: Declaration(klass=DC.NET_QUANTITY, raw="Net wt 400 g",
            norm={"value": 400.0, "unit": "g", "unit_base": "g", "value_base": 400.0,
                  "is_canonical": True}),
        DC.RETAIL_SALE_PRICE: Declaration(klass=DC.RETAIL_SALE_PRICE, raw="MRP Rs 135",
            norm={"value": 135.0, "count": 1, "has_tax_clause": True}),
        DC.DATE_OF_PACKING: Declaration(klass=DC.DATE_OF_PACKING, raw="PKD 07/2026",
            norm={"year": 2026, "month": 7, "iso": "2026-07"}),
    }


def run(engine, pkg):
    return engine.evaluate_all(scan(), pkg, declarations(), {},
                               as_of=date(2026, 7, 1))


def test_unconfirmed_facts_are_reported_with_the_checks_they_hold_up(engine):
    pkg = package()
    findings = run(engine, pkg)
    hints = unblock.pending(engine, scan(), pkg, declarations(), {}, findings,
                            as_of=date(2026, 7, 1))
    assert hints, "some check must be waiting on a confirmable fact"
    keys = {hint.key for hint in hints}
    # Import status is the whole of the origin rule's gate and package structure
    # is the whole of the unit-price gate, so both are offered.
    assert {"origin", "bundle_type"} <= keys
    # The commodity category is *not*, and that is the derivation earning its
    # keep. The category does gate the food best-before screen -- but this scan
    # is a partial capture, so that screen could not conclude the date absent
    # even once told the package is food. Sending an officer to tick a box that
    # decides nothing is exactly what a hand-written list of "facts each rule
    # consults" would have done here.
    assert "category" not in keys
    for hint in hints:
        assert hint.count == len(hint.rule_ids) > 0
        assert hint.action and hint.label


def test_the_category_is_offered_once_the_capture_could_act_on_it(engine):
    """The same fact, the same rule, a capture that can prove absence."""
    complete = scan().model_copy(update={"coverage": EvidenceCoverage(
        panels_captured=[Panel.PDP, Panel.BACK],
        completeness=CaptureCompleteness.COMPLETE, attested_complete=True,
        frames=2, lines_read=40, legible_lines=38, mean_confidence=0.9)})
    pkg = package()
    findings = engine.evaluate_all(complete, pkg, declarations(), {}, as_of=date(2026, 7, 1))
    hints = unblock.pending(engine, complete, pkg, declarations(), {}, findings,
                            as_of=date(2026, 7, 1))
    category = next((h for h in hints if h.key == "category"), None)
    assert category is not None
    assert category.rule_ids == ["LMPCR.R6.1.D.EXPIRY_FOOD"]


def test_a_fact_already_confirmed_is_not_offered_again(engine):
    pkg = package(shape="rectangular", shape_confirmed=True)
    findings = run(engine, pkg)
    hints = unblock.pending(engine, scan(), pkg, declarations(), {}, findings,
                            as_of=date(2026, 7, 1))
    assert "shape" not in {hint.key for hint in hints}


def test_every_named_rule_really_is_undecided_now_and_decided_then(engine):
    """The promise on screen has to hold, or it is worse than saying nothing."""
    pkg = package()
    findings = run(engine, pkg)
    undecided = {f.rule_id for f in findings
                 if f.verdict in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED)}
    hints = unblock.pending(engine, scan(), pkg, declarations(), {}, findings,
                            as_of=date(2026, 7, 1))
    for hint in hints:
        assert set(hint.rule_ids) <= undecided
        released = set()
        for assumption in unblock.FACTS[hint.key]["values"]:
            trial = pkg.model_copy(update={
                "legal_context": {**pkg.legal_context, **assumption}})
            released |= {f.rule_id for f in run(engine, trial)
                         if f.rule_id in undecided
                         and f.verdict not in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED)}
        assert set(hint.rule_ids) <= released


def test_nothing_is_suggested_when_nothing_is_undecided(engine):
    pkg = package()
    decided = [f for f in run(engine, pkg)
               if f.verdict not in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED)]
    assert unblock.pending(engine, scan(), pkg, declarations(), {}, decided,
                           as_of=date(2026, 7, 1)) == []
