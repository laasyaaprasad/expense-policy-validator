"""M11 gate: the evaluation, checked against answers known in advance."""

import json

import pytest

from evaluation.evaluate import METRICS_PATH, build, load_results, score_receipts, to_json
from evaluation.gold import load_gold
from evaluation.oracle import ORACLE_PATH
from validator.pipeline import OUTPUT_DIR

GOLD = load_gold()


@pytest.fixture(scope="module")
def v1():
    return load_results(OUTPUT_DIR / "results.jsonl")


def test_the_confusion_matrix_covers_the_28_with_the_known_gold_split(v1):
    matrix = score_receipts(v1, GOLD)["disposition"]["matrix"]
    assert sum(map(sum, matrix)) == 28
    assert [sum(row) for row in matrix] == [16, 3, 9]  # flag, review, pass


def test_oracle_outputs_score_perfectly():
    s = score_receipts(load_results(ORACLE_PATH), GOLD)
    d = s["disposition"]
    assert d["matrix"] == [[16, 0, 0], [0, 3, 0], [0, 0, 9]]
    assert d["false_positives"]["k"] == d["missed_violations"]["k"] == 0
    assert s["exceptions"]["matched"] == s["exceptions"]["gold"] == s["exceptions"]["evidence_correct"]["k"]


def test_a_flagged_pass_receipt_is_a_false_positive_and_a_passed_flag_is_a_miss(v1):
    records = json.loads(json.dumps(v1))
    records["RCP-001"]["disposition"] = "flag"  # gold: pass
    records["RCP-012"]["disposition"] = "pass"  # gold: flag
    d = score_receipts(records, GOLD)["disposition"]
    assert (d["false_positives"]["k"], d["missed_violations"]["k"]) == (1, 1)
    assert {w["receipt_id"] for w in d["wrong"]} == {"RCP-001", "RCP-012"}


def test_committed_metrics_are_current():
    assert METRICS_PATH.read_text(encoding="utf-8") == to_json(build())
