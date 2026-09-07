"""The acceptance matrix, headless.

Same scenario definitions the browser runs, so the UI and CI cannot disagree
about what the system is supposed to do. Runs under the fixture engine: exact
geometry, no model download, no network -- a failure here is a bug in the rules
or the metrology, never in the recognition.
"""

from __future__ import annotations

import pytest

from tula import bench
from tula.rules.engine import RulesEngine


@pytest.fixture(scope="module")
def rules():
    return RulesEngine.from_directory()


@pytest.fixture(scope="module")
def results(rules, tmp_path_factory):
    work = tmp_path_factory.mktemp("bench")
    return {
        s.key: bench.run_scenario(s, engine_name="fixture", rules=rules, work_dir=work)
        for s in bench.SCENARIOS
    }


@pytest.mark.parametrize("key", [s.key for s in bench.SCENARIOS])
def test_scenario_meets_its_expectations(results, key):
    result = results[key]
    mismatches = [
        f"{c.rule_id}: expected {c.expected}, got {c.actual}" for c in result.failed
    ]
    assert not mismatches, "; ".join(mismatches)


@pytest.mark.parametrize("key", [s.key for s in bench.SCENARIOS])
def test_measurement_contains_the_drawn_truth(results, key):
    """The metrology claim, made falsifiable.

    The renderer knows the cap height it drew. If the reported interval does not
    contain it, the uncertainty is understated and every Rule 8 verdict built on
    it is unsafe.
    """
    for check in results[key].truth_checks:
        if check.measured is None:
            continue
        assert check.within_interval, (
            f"{key}/{check.quantity}: drew {check.drawn:.3f} but reported "
            f"{check.measured.value:.3f} ± {check.measured.uncertainty:.3f}"
        )


def test_every_scenario_asserts_something():
    for scenario in bench.SCENARIOS:
        assert scenario.expect, f"{scenario.key} asserts nothing"
        assert scenario.proves, f"{scenario.key} does not say what it proves"


def test_scenario_keys_are_unique():
    keys = [s.key for s in bench.SCENARIOS]
    assert len(set(keys)) == len(keys)


def test_engine_aware_expectations_are_applied():
    compliant = bench.scenario("compliant")
    assert compliant.expectations("fixture")[bench.R_BILINGUAL] == "PASS"
    # a recogniser without Devanagari cannot establish Rule 9(3) compliance
    assert compliant.expectations("rapidocr")[bench.R_BILINGUAL] == "PASS"
