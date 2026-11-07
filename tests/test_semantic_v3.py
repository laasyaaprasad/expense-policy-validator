"""Experiment 8 (experiments/semantic_v3/PLAN.md): version 3's rules on small hand-made vectors, offline."""

import json
from pathlib import Path

import numpy as np
import pytest

from experiments.semantic_first.semantic import Anchor
from experiments.semantic_v3.system import Classifier, Params

RESULTS = Path(__file__).resolve().parent.parent / "experiments" / "semantic_v3" / "results"


def unit(*v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


CURATED = [Anchor("food_beverage", "en", "Pepperoni pizza", "typical"), Anchor("ground_transport", "en", "Taxi fare", "typical"),
           Anchor("alcohol", "en", "Bottle of red wine", "typical")]
VECTORS = np.stack([unit(1, 0, 0, 0), unit(0, 1, 0, 0), unit(0, 0, 1, 0)])
PARAMS = Params(tau_mem=0.97, tau_fam=0.8, delta=0.1, safe_cutoff=0.05, memory_confidence=0.995, curated_confidence=0.99)


def system() -> Classifier:
    return Classifier(CURATED, VECTORS, PARAMS)


def test_a_clear_familiar_safe_line_is_decided_by_the_curated_library():
    d = system().decide("Pizza slice", unit(1, 0.05, 0, 0), "5812")
    assert d.decided and d.step == "curated" and d.category == "food_beverage"


def test_restricted_categories_always_go_to_the_model():
    d = system().decide("Glass of wine", unit(0, 0, 1, 0.05), "5812")
    assert not d.decided and d.category == "alcohol" and "always go to the model" in d.why


def test_an_unfamiliar_line_goes_to_the_model():
    d = system().decide("MAKOLI", unit(0.6, 0, 0.3, 0.74), "5812")
    assert not d.decided and d.why.startswith("unfamiliar")


def test_a_line_close_to_a_restricted_reference_goes_to_the_model():
    d = system().decide("Wine-braised beef", unit(1, 0, 0.9, 0), "5812")
    assert not d.decided and (d.why.startswith("near a restricted") or d.why.startswith("unfamiliar"))


def test_memory_decides_near_repeats_only():
    s = system()
    assert s.learn("es teh manis", unit(0, 0, 0, 1), "food_beverage", True, "food_beverage", 0.99) == "memory"
    assert s.decide("ES TEH MANIS", unit(0, 0, 0.01, 1), "5812").step == "memory"
    assert s.decide("milo", unit(0, 0, 0.6, 0.8), "5812").step != "memory"  # similar, not a near-repeat


def test_learning_needs_two_keys_and_restricted_lines_become_risk_references():
    s = system()
    assert s.learn("x", unit(0, 0, 0, 1), "food_beverage", False, "food_beverage", 0.99).startswith("left out: unsure")
    assert s.learn("x", unit(0, 0, 0, 1), "food_beverage", True, "other", 0.99).startswith("left out: the model disagrees")
    assert s.learn("x", unit(0, 0, 0, 1), "food_beverage", True, "food_beverage", 0.6).startswith("left out: the model disagrees")
    assert s.learn("makoli", unit(0, 0.1, 0, 1), "alcohol", True, "food_beverage", 0.99) == "risk"
    # A near-repeat of a remembered food line is blocked when a restricted reference is just as close.
    s.learn("es teh", unit(0, 0.1, 0.01, 1), "food_beverage", True, "food_beverage", 0.99)
    d = s.decide("MAKOLI", unit(0, 0.1, 0, 1), "5812")
    assert not d.decided


def test_code_checks_come_first():
    assert system().decide("25%", unit(1, 0, 0, 0), "5812").step == "code check"


@pytest.mark.skipif(not (RESULTS / "metrics.json").exists(), reason="the experiment has not been run")
def test_the_committed_verdict_follows_from_the_bar():
    m = json.loads((RESULTS / "metrics.json").read_text())
    assert m["verdict"] == ("passes" if all(m["bar"].values()) else "fails: " + "; ".join(k for k, ok in m["bar"].items() if not ok))
