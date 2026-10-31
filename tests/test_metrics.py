"""Metric code against cases worked out by hand."""

import pytest

from evaluation.metrics import binary_f1, macro_f1, per_category, recipient_matches

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
