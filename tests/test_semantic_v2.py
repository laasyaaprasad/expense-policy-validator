"""Experiment 7 (experiments/semantic_v2/PLAN.md): the code checks, the per-kind cutoffs and the learning library.

Offline: the lookup is tested on small hand-made vectors, so neither the model nor PyTorch is needed.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from experiments.semantic_first.semantic import Anchor
from experiments.semantic_v2.__main__ import learned_library
from experiments.semantic_v2.lookup import Lookup, code_check, load_library

RESULTS = Path(__file__).resolve().parent.parent / "experiments" / "semantic_v2" / "results"


@pytest.mark.parametrize("text, mcc, sends_to_model", [
    ("25%", "5812", True),
    ("SB 1 OR", "5812", True),
    ("+M", "5812", True),
    ("緑茶", "5812", False),
    ("Club sandwich", "5812", False),
    ("Club sandwich", "5411", True),  # a supermarket
    ("Ibuprofen 200mg", "5912", True),  # a pharmacy
    ("Lunch for Ms. Tan", "5812", True),
])
def test_code_checks(text, mcc, sends_to_model):
    assert (code_check(text, mcc) is not None) == sends_to_model


def lookup(groups: list[tuple[str, list[float], int]]) -> Lookup:
    rows = [(category, [x + 0.01 * i * (k == 1) for k, x in enumerate(v)]) for category, v, n in groups for i in range(n)]
    vectors = np.array([v for _, v in rows], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return Lookup([Anchor(c, "en", f"{c} item {i}", "typical") for i, (c, _) in enumerate(rows)], vectors)


def test_a_restricted_category_needs_a_cutoff_at_least_as_strict_as_a_safe_one():
    lk = lookup([("food_beverage", [1, 0, 0], 12), ("computer_accessory", [0, 1, 0], 12), ("alcohol", [0, 0, 1], 12)])
    assert lk.cutoff["restricted"] >= lk.cutoff["safe"]
    [d] = lk.decide(["a plain line"], np.array([[0.0, 0.05, 1.0]]) / np.linalg.norm([0.0, 0.05, 1.0]), ["5812"])
    assert d.decided and d.category == "alcohol" and 0.5 < d.confidence <= 1


def test_the_merchant_check_sends_a_clear_line_to_the_model():
    lk = lookup([("food_beverage", [1, 0, 0], 12), ("computer_accessory", [0, 1, 0], 12)])
    [d] = lk.decide(["Club sandwich"], np.array([[1.0, 0.0, 0.0]]), ["5411"])
    assert not d.decided and "supermarket" in d.why


def test_the_learning_library_adds_reviewed_lines_once_and_skips_conflicts():
    library = [Anchor("food_beverage", "en", "Iced tea", "typical")]
    labels = {
        "A-L1": {"description_raw": "ES TEH", "category": "food_beverage", "sure": True},
        "B-L1": {"description_raw": "es teh", "category": "food_beverage", "sure": True},
        "C-L1": {"description_raw": "BOX", "category": "other", "sure": True},
        "D-L1": {"description_raw": "box", "category": "food_beverage", "sure": True},
        "E-L1": {"description_raw": "Mystery", "category": "other", "sure": False},
        "F-L1": {"description_raw": "ICED TEA", "category": "food_beverage", "sure": True},
    }
    added = learned_library(library, labels)[1:]
    assert [(a.text, a.category, a.source) for a in added] == [("es teh", "food_beverage", "reviewed")]


def test_the_library_has_the_hard_negatives_and_no_duplicates():
    library = load_library()
    assert len({a.text for a in library}) == len(library)
    assert sum(a.source == "hard negative" for a in library) == 22


@pytest.mark.skipif(not (RESULTS / "metrics.json").exists(), reason="the experiment has not been run")
def test_the_committed_verdict_follows_from_the_bar():
    m = json.loads((RESULTS / "metrics.json").read_text())
    assert m["verdict"] == ("passes" if all(m["bar"].values()) else "fails: " + "; ".join(k for k, ok in m["bar"].items() if not ok))
    assert m["library"]["kept"] + len(m["library"]["dropped"]) == m["library"]["written"]
