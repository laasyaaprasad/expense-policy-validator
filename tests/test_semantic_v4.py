"""Experiment 12 (experiments/semantic_v4): the word rules and the blind trap set. Offline: no model calls."""

import json

import pytest

from validator.data import ROOT, load_receipts
from validator.lookup import Lookup, item_words

HERE = ROOT / "experiments" / "semantic_v4"


@pytest.mark.parametrize("text, words", [
    ("Cream [L]", ("cream",)),
    ("SOJU PEACH 360ML", ("peach", "soju")),
    ("Teh Manis Es", ("es", "manis", "teh")),
    ("25%", ()),
])
def test_item_words_drop_units_size_codes_and_numbers(text, words):
    assert item_words(text) == words


def test_exact_words_never_decide_by_resemblance():
    lookup = Lookup(overrides={"memory_rule": "exact_words"})
    lookup._load_vectors()
    memory_text, category = lookup.memory[0]
    d = lookup._decide_exact(memory_text.upper())
    assert d.decided and d.category == category
    assert not lookup._decide_exact(f"Arak {memory_text}").decided  # one extra word: to the model


def test_the_word_score_needs_every_word_on_both_sides():
    import numpy as np
    lookup = Lookup()
    e = np.eye(3, dtype=np.float32)
    lookup._wv = {"arak": e[0], "bali": e[1], "nasi": e[2]}  # hand-set vectors: nothing is embedded
    assert lookup.word_score(("bali", "nasi"), ("bali", "nasi")) == pytest.approx(1.0)
    assert lookup.word_score(("arak", "bali"), ("bali", "nasi")) == 0.0  # "arak" unexplained, and "nasi"
    assert lookup.word_score(("bali",), ("bali", "nasi")) == 0.0  # the other side's "nasi" unexplained
    assert lookup.word_score((), ("bali",)) == 0.0


def test_the_blind_trap_set_is_build_py_minus_the_listed_drops():
    from experiments.semantic_v4.traps.build import DROPPED
    lines = json.loads((HERE / "traps" / "lines.json").read_text())
    labels = [json.loads(x) for x in (HERE / "traps" / "labels.jsonl").read_text().splitlines()]
    assert sorted(x["description_raw"] for x in labels) == sorted(x["text"] for x in lines if x["text"] not in DROPPED)
    assert (len(labels), sum(x["restricted"] for x in labels)) == (79, 57)
    assert {r.mcc for r in load_receipts(HERE / "traps" / "receipts.jsonl")} == {"5812"}


def test_version_4_stays_out_of_the_pipeline_after_failing_its_bar():
    metrics = json.loads((HERE / "results" / "metrics.json").read_text())
    assert metrics["verdict"].startswith("fails: 4 meaning adds something")
    assert Lookup().config.get("memory_rule") is None
