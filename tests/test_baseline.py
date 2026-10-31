"""The keyword matcher on synthetic strings (never receipt text), and determinism."""

import pytest

from validator.baseline import BASELINE_PATH, fold, load_terms, match, run_baseline
from validator.reporter import to_json_line


def category(text):
    hit = match(text)
    return hit.category if hit else None


@pytest.mark.parametrize("text, want", [
    ("Ginger ale", "alcohol"),  # whole-word "ale": a false alarm no word list avoids
    ("Ginger biscuits", None),  # "gin" is not
    ("Drum lessons", None),  # nor is "rum"
    ("Tonic and gin", "alcohol"),
    ("Two beers", "alcohol"),  # plurals match
    ("Box of cigars", "tobacco"),
    ("Silk ties", "clothing"),
    ("Ring binder", "jewelry"),  # a keyword list's own blind spot, kept on purpose
])
def test_whole_words_and_plurals(text, want):
    assert category(text) == want


def test_accents_and_case_are_folded():
    assert fold("Bière BLONDE") == "biere blonde"
    assert category("BIÈRE blonde") == "alcohol"
    assert category("Weißwein Schorle") == "alcohol"  # ß folds to ss on both sides


def test_japanese_matches_as_a_substring():
    assert category("生ビール中ジョッキ") == "alcohol"
    assert category("ワイヤレスイヤホン") == "personal_electronics"


def test_restricted_beats_unrestricted_and_longer_beats_shorter():
    assert category("Chocolate cake with rum") == "alcohol"  # restricted wins whatever its length
    assert category("Bookshop gift card") == "gift_card"  # "gift card" is longer than "gift"
    assert category("Pressed juice") == "food_beverage"


def test_every_category_is_in_the_taxonomy():
    assert load_terms()  # raises on an unknown category


def test_two_runs_are_identical():
    first, second = [to_json_line(r) for r in run_baseline()], [to_json_line(r) for r in run_baseline()]
    assert first == second


def test_committed_baseline_is_current():
    assert BASELINE_PATH.read_text(encoding="utf-8").splitlines() == [to_json_line(r) for r in run_baseline()]
