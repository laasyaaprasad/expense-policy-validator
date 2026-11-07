"""Experiment 6 (experiments/semantic_first/PLAN.md): the anchors, decontamination, gates and the semantic stage's rules.

Offline: the stage is tested on small hand-made vectors, and the committed results are checked as written.
"""

import json
from collections import Counter

import numpy as np
import pytest

from evaluation import cord
from evaluation.dev import load_dev
from experiments.paraphrase.__main__ import load_variants, reword
from experiments.semantic_first.semantic import (ALWAYS_MODEL, TAXONOMY, Anchor, SemanticStage, decontaminate, gate,
                                                 load_anchors, near_copy)
from validator.data import load_receipts

RESULTS = __import__("pathlib").Path(__file__).resolve().parent.parent / "experiments" / "semantic_first" / "results"


def test_every_anchor_names_a_real_category_and_each_category_has_at_least_ten():
    anchors = load_anchors()
    assert {a.category for a in anchors} == set(TAXONOMY)
    assert len({a.text for a in anchors}) == len(anchors)
    assert min(Counter(a.category for a in anchors).values()) >= 10
    assert {a.source for a in anchors} <= {"definition", "prompt rule", "typical"}


def test_near_copies_are_caught_and_different_items_are_not():
    assert near_copy("Leather belt", "leather  belt")
    assert near_copy("HDMI cable 2m", "HDMI cable 3m")
    assert not near_copy("Pepperoni pizza", "Garlic bread")
    assert not near_copy("Wireless mouse", "Wireless earbuds charging case")


def test_no_kept_anchor_copies_a_test_line():
    main = load_receipts()
    dev, _ = load_dev()
    texts = [line.description_raw for rs in (main, dev, reword(main, load_variants()), load_receipts(cord.RECEIPTS_PATH))
             for r in rs for line in r.line_items]
    kept, dropped = decontaminate(load_anchors(), texts)
    assert dropped and len(kept) + len(dropped) == len(load_anchors())
    assert not any(near_copy(a.text, t) for a in kept for t in texts)


@pytest.mark.parametrize("text, fires", [
    ("Bottle of wine for Ms. Tan", True),
    ("Fruit basket for J. Okafor", True),
    ("Corporate present, engraved pen", True),
    ("Theatre tickets x2", True),
    ("Prescription eye drops", True),
    ("Refurbished smartphone", True),
    ("Train tickets London to Leeds", False),
    ("Club sandwich", False),
    ("Matcha latte", False),
])
def test_text_gates(text, fires):
    assert (gate(text) is not None) == fires


def stage(anchors: list[tuple[str, list[float]]]) -> SemanticStage:
    vectors = np.array([v for _, v in anchors], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return SemanticStage("test", [Anchor(c, "en", f"{c} {i}", "typical") for i, (c, _) in enumerate(anchors)], vectors)


def unit(*v):
    v = np.array([v], dtype=np.float32)
    return v / np.linalg.norm(v)


CLUSTERS = [("food_beverage", [1, 0.02 * i, 0]) for i in range(12)] + [("computer_accessory", [0, 1, 0.02 * i]) for i in range(12)] \
         + [("alcohol", [0.02 * i, 0, 1]) for i in range(12)]


def test_a_clear_line_is_decided_with_a_confidence_from_the_anchors():
    s = stage(CLUSTERS)
    [d] = s.decide(["a plain line"], unit(0, 1, 0.01))
    assert d.decided and d.category == "computer_accessory" and 0.5 < d.confidence < 1
    assert s.cutoff == 0.0 and s.loo_decided == s.loo_right == len(CLUSTERS)


def test_the_restricted_guard_sends_a_line_between_food_and_alcohol_to_the_model():
    [d] = stage(CLUSTERS).decide(["a plain line"], unit(1, 0, 0.9))
    assert not d.decided and d.why.startswith("restricted guard")


def test_categories_that_need_the_model_and_text_gates_are_never_decided():
    s = stage(CLUSTERS + [("other", [-1, 0, 0]) for _ in range(3)])
    [d] = s.decide(["a plain line"], unit(-1, 0, 0))
    assert d.category in ALWAYS_MODEL and not d.decided
    [d] = s.decide(["Lunch for Mr. Ito"], unit(1, 0, 0))
    assert not d.decided and d.why.startswith("text gate")


def test_the_cutoff_rises_until_the_anchors_decided_are_right():
    # A food anchor sits inside the computer cluster, so a low cutoff decides it wrongly.
    s = stage(CLUSTERS + [("food_beverage", [0.05, 1, 0.01])])
    assert s.cutoff > 0 and s.loo_decided == s.loo_right
    [d] = s.decide(["a plain line"], unit(0.05, 1, 0.01))
    assert not d.decided and d.why.startswith("margin")


def test_confidence_never_falls_as_the_margin_rises_and_stays_above_the_review_threshold():
    s = stage(CLUSTERS + [("food_beverage", [0.05, 1, 0.01])])
    confidences = [s.confidence(m) for m in np.linspace(s.cutoff, 2, 30)]
    assert all(a <= b for a, b in zip(confidences, confidences[1:]))
    assert all(0.5 < c <= 1 for c in confidences)


@pytest.mark.skipif(not (RESULTS / "metrics.json").exists(), reason="the experiment has not been run")
def test_the_committed_verdict_follows_from_the_bar():
    m = json.loads((RESULTS / "metrics.json").read_text())
    for arm, checks in m["bar"].items():
        assert m["verdict"][arm] == ("passes" if all(checks.values()) else "fails: " + "; ".join(k for k, ok in checks.items() if not ok))
    assert m["anchors"]["kept"] + len(m["anchors"]["dropped"]) == m["anchors"]["written"]
