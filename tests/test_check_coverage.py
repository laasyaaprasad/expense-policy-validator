"""Switching off any one rule check makes an answer-key test fail.

Each check in turn is replaced by one that never raises, and the two suites with known answers are
rerun: the 28 in-scope receipts on gold categories (test_oracle.py), and the hand-written scenarios
(test_synthetic_receipts.py). A check that neither notices would be untested.
"""

import pytest

import tests.test_oracle as oracle
import tests.test_synthetic_receipts as synthetic
from evaluation.gold import load_gold
from evaluation.oracle import run_oracle
from validator.engine.rules import CHECKS


def fails(test, *args) -> bool:
    try:
        test(*args)
    except AssertionError:
        return True
    return False


@pytest.mark.parametrize("rule_id", sorted(CHECKS))
def test_switching_off_the_check_fails_a_test(rule_id, monkeypatch):
    monkeypatch.setitem(CHECKS, rule_id, lambda rule, receipt, ctx: None)
    assert fails(oracle.test_every_in_scope_receipt_matches_the_answer_key, run_oracle(), load_gold()) or any(
        fails(synthetic.test_scenario, scenario) for scenario in synthetic.SCENARIOS
    )


def test_the_suites_pass_with_every_check_on():
    # Without this, a suite that always failed would make every check above look tested.
    assert not fails(oracle.test_every_in_scope_receipt_matches_the_answer_key, run_oracle(), load_gold())
    assert not any(fails(synthetic.test_scenario, scenario) for scenario in synthetic.SCENARIOS)
