"""Experiment 11 (experiments/semantic_v33): version 3.3's options and the trap set. Offline: no model calls."""

import json

import pytest

from validator.data import ROOT, load_receipts
from validator.lookup import Lookup, restricted_word

HERE = ROOT / "experiments" / "semantic_v33"


@pytest.mark.parametrize("text, category", [
    ("Coconut rum", "alcohol"), ("Lemon dish soap", "household_goods"), ("Onion rings large", "jewelry"),
    ("Coconut water", None), ("Sampoerna Mild 16", None),
])
def test_the_restricted_word_check_uses_the_frozen_keyword_list(text, category):
    hit = restricted_word(text)
    assert (hit.category if hit else None) == category


def test_the_pipeline_config_leaves_version_3_3_switched_off():
    config = Lookup().config
    assert not any(config.get(k) for k in ("restricted_words", "block_same_item", "library_agrees", "block_tau"))


def test_the_trap_set_is_what_build_py_writes_and_copies_no_seen_line():
    from experiments.semantic_v33.traps.build import RESTRICTED, SAFE
    labels = [json.loads(x) for x in (HERE / "traps" / "labels.jsonl").read_text().splitlines()]
    assert sorted(x["description_raw"] for x in labels) == sorted(t for ts in (*RESTRICTED.values(), *SAFE.values()) for t in ts)
    assert sum(x["restricted"] for x in labels) == 66 and len(labels) == 88 and all(x["sure"] for x in labels)
    receipts = load_receipts(HERE / "traps" / "receipts.jsonl")
    assert {r.mcc for r in receipts} == {"5812"}
    seen = {line.description_raw.lower() for d in ("cord", "cord/test", "cord/train", "cord/train2", "cord/train3")
            for r in load_receipts(ROOT / d / "receipts.jsonl") for line in r.line_items}
    assert not {x["description_raw"].lower() for x in labels} & seen
