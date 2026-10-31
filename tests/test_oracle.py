"""On the correct categories, with every rule enabled, the engine reproduces the answer key.

The inputs are perfect here, so a failure is rule logic and nothing else. The snapshot in
outputs/oracle.jsonl is committed; any change to engine behaviour on the real data fails the
snapshot test until the snapshot is regenerated on purpose (`make oracle`).
"""

import pytest

from evaluation.gold import in_scope, load_gold
from evaluation.oracle import ORACLE_PATH, compare, oracle_results, run_oracle
from validator.reporter import to_json_line


@pytest.fixture(scope="module")
def decisions():
    return run_oracle()


@pytest.fixture(scope="module")
def gold():
    return load_gold()


def test_every_in_scope_receipt_matches_the_answer_key(decisions, gold):
    differences = {d.receipt_id: compare(d, gold[d.receipt_id]) for d in decisions if in_scope(d.receipt_id)}
    assert len(differences) == 28
    assert {receipt_id: c for receipt_id, c in differences.items() if not c.matches} == {}


def test_out_of_scope_differences_are_only_the_timing_rules(decisions, gold):
    # TIME-01 to 04 are not implemented. TIME-03 "replaces the threshold rule being split around",
    # so on the split pair RCP-020/021 we raise the meal cap it would have replaced.
    for d in decisions:
        if in_scope(d.receipt_id):
            continue
        c = compare(d, gold[d.receipt_id])
        assert all(rule.startswith("TIME-") for rule in c.missing + c.missing_signals), d.receipt_id
        assert c.extra_signals == [] and c.wrong_lines == {}, d.receipt_id
        assert c.extra in ([], ["MEAL-01"]), d.receipt_id


def test_output_matches_the_committed_snapshot():
    current = [to_json_line(result) for result in oracle_results()]
    assert current == ORACLE_PATH.read_text(encoding="utf-8").splitlines()
