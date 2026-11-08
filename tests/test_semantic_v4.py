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


def test_version_4_is_the_pipelines_rule_with_experiment_12s_calibration():
    metrics = json.loads((HERE / "results" / "metrics.json").read_text())
    config = Lookup().config
    assert {k: config[k] for k in ("memory_rule", "tau_word", "block_tau")} == metrics["config"]
    # Adopted by the user's choice despite bar 4 (exact words decided 11 more replay lines); every safety bar passed.
    assert metrics["verdict"] == "fails: 4 meaning adds something: V4 decides more replay lines than A"
    assert all(ok for k, ok in metrics["bar"].items() if not k.startswith("4"))


def test_the_pipeline_replays_offline_every_vector_it_needs_is_committed():
    from evaluation.dev import load_dev
    from experiments.paraphrase.__main__ import load_variants, reword
    from validator.normaliser import normalise
    lookup = Lookup()
    main_receipts = load_receipts()
    lines = [line.description_raw for rs in (main_receipts, load_dev()[0], reword(main_receipts, load_variants()))
             for r in rs for line in r.line_items]
    refs = [t for t, _ in lookup.library + lookup.memory + lookup.risk]
    texts = [normalise(t) for t in refs + lines] + [w for t in refs + lines for w in item_words(t)]
    assert all(lookup.embedder.key(t) in lookup.embedder._cache for t in texts)


def test_on_the_32_receipts_the_lookup_decides_only_soup_of_the_day():
    from validator.lookup import default_lookup
    decided = {lid: d for r in load_receipts() for lid, d in default_lookup().decide_receipt(r).items() if d.decided}
    assert list(decided) == ["RCP-021-L2"] and decided["RCP-021-L2"].category == "food_beverage"
    assert decided["RCP-021-L2"].nearest[0][0] == "Soup Of The Day"
