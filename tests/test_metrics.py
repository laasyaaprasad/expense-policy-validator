"""Metric code against cases worked out by hand."""

import pytest

from evaluation.metrics import (binary_f1, confusion, false_positives, macro_f1, missed_violations, per_category,
                                recipient_matches, rule_scores, wilson)

# a: 1 of 2 found, nothing wrongly put in it    → P 1,   R 1/2, F1 2/3
# b: its 1 item found, plus 1 wrongly put in it  → P 1/2, R 1,   F1 2/3
# c: its 1 item missed, nothing put in it        → P 0,   R 0,   F1 0
# d: predicted once, never an answer             → no score of its own
TRUE = ["a", "a", "b", "c"]
PRED = ["a", "b", "b", "d"]


def test_macro_f1_averages_over_answer_categories_only():
    assert macro_f1(TRUE, PRED) == pytest.approx((2 / 3 + 2 / 3 + 0) / 3)


def test_per_category_counts():
    scores = {s.category: s for s in per_category(TRUE, PRED)}
    assert set(scores) == {"a", "b", "c"}
    assert (scores["a"].support, scores["a"].predicted, scores["a"].correct) == (2, 1, 1)
    assert (scores["b"].precision, scores["b"].recall) == (0.5, 1.0)
    assert scores["c"].f1 == 0.0 and scores["c"].predicted == 0


def test_perfect_and_hopeless():
    assert macro_f1(TRUE, TRUE) == 1.0
    assert macro_f1(TRUE, ["z"] * 4) == 0.0


def test_restricted_f1_counts_restricted_as_positive():
    # one restricted item found, one missed, one false alarm: 2·1 / (2·1 + 1 + 1)
    assert binary_f1([True, True, False, False, False], [True, False, True, False, False]) == pytest.approx(0.5)
    # all-unrestricted answers and predictions: nothing to find, so 0 by convention
    assert binary_f1([False, False], [False, False]) == 0.0


@pytest.mark.parametrize("got, want, ok", [
    (("R. Delgado", "government_official"), ("R. Delgado", "government_official"), True),
    (("Mr R. Delgado", "government_official"), ("R. Delgado", "government_official"), True),
    (("R. Delgado", "client"), ("R. Delgado", "government_official"), False),
    (("A. Moreau", "client"), (None, None), False),
    ((None, None), (None, None), True),
])
def test_recipient_matching(got, want, ok):
    assert recipient_matches(*got, *want) is ok


def test_a_gold_category_never_predicted_scores_zero_and_a_predicted_only_one_is_ignored():
    # Two answer categories; "b" is never predicted, "z" is only predicted.
    assert macro_f1(["a", "b"], ["a", "z"]) == pytest.approx((1.0 + 0.0) / 2)


def test_confusion_rows_are_gold_and_columns_ours():
    gold, ours = ["flag", "flag", "pass", "review"], ["flag", "review", "flag", "review"]
    assert confusion(gold, ours) == [[1, 1, 0], [0, 1, 0], [1, 0, 0]]


def test_false_positives_and_missed_violations():
    gold, ours = ["pass", "pass", "pass", "flag", "flag", "review"], ["pass", "review", "flag", "review", "flag", "pass"]
    assert false_positives(gold, ours) == (2, 3)
    assert missed_violations(gold, ours) == (1, 2)


def test_wilson_interval_for_one_in_nine():
    low, high = wilson(1, 9)
    assert low == pytest.approx(0.020, abs=0.001) and high == pytest.approx(0.435, abs=0.001)
    assert wilson(0, 9)[0] == 0.0 and wilson(0, 9)[1] > 0.25  # zero errors is not zero uncertainty


def test_rule_matching_follows_the_data_dictionary():
    gold = {"R1": {"ALC-01": ("L1",), "MEAL-01": ()}, "R2": {"TECH-01": ("L2",)}}
    ours = {"R1": {"ALC-01": ("L1", "L3"), "MEAL-01": ()}, "R2": {"GIFT-02": ("L2",)}}
    scores = {s.rule_id: s for s in rule_scores(gold, ours)}
    # right rule, wrong lines: matched, but its evidence is not correct
    assert (scores["ALC-01"].matched, scores["ALC-01"].evidence_correct) == (1, 0)
    # whole-receipt rule: both empty counts as correct evidence
    assert (scores["MEAL-01"].matched, scores["MEAL-01"].evidence_correct) == (1, 1)
    # a gold rule we never raise: recall 0, precision n/a
    assert (scores["TECH-01"].recall, scores["TECH-01"].precision) == (0.0, None)
    # a rule we raise that gold never does: precision 0, recall n/a
    assert (scores["GIFT-02"].precision, scores["GIFT-02"].recall) == (0.0, None)
