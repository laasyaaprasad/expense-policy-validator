"""The semantic lookup in the pipeline (validator/lookup.py; DECISIONS.md, section 12). Offline: committed vectors only."""

import numpy as np
import pytest

from validator.data import load_receipts
from validator.lookup import Lookup, code_check, default_lookup

RECEIPTS = {r.receipt_id: r for r in load_receipts()}


@pytest.mark.parametrize("text, mcc, to_model", [
    ("-TIKET", "5812", True),  # experiment 8's one real miss
    ("Train tickets", "5812", False),
    ("25%", "5812", True),
    ("Club sandwich", "5411", True),
    ("Club sandwich", "5812", False),
])
def test_code_checks(text, mcc, to_model):
    assert (code_check(text, mcc) is not None) == to_model


def test_the_lookup_never_decides_a_restricted_category_on_the_32_receipts():
    lookup = default_lookup()
    from validator.taxonomy import load_taxonomy
    restricted = load_taxonomy()
    decided = [(rid, d) for rid, r in RECEIPTS.items() for d in lookup.decide_receipt(r).values() if d.decided]
    assert decided and not any(restricted[d.category] for _, d in decided)
    assert {d.step for _, d in decided} <= {"memory", "library"}


def test_without_the_model_installed_an_uncached_line_goes_to_the_model(monkeypatch, tmp_path):
    lookup = Lookup()
    monkeypatch.setattr(type(lookup.embedder), "available", lambda self: False)
    lookup._load_vectors()
    [d] = [lookup._decide("A line nobody has embedded before", None, "5812")]
    assert not d.decided and "isn't installed" in d.why


def test_memory_comes_only_from_reviewed_training_receipts_and_the_library_copies_no_test_line():
    import csv
    from validator.lookup import LOOKUP_DIR
    with (LOOKUP_DIR / "memory.csv").open(encoding="utf-8") as f:
        assert all(row["reviewed_line"].startswith("CORD-R") for row in csv.DictReader(f))
    # The hand-written library was decontaminated; memory may repeat a common item ("Iced tea"), learned from other receipts.
    own = {line.description_raw.lower() for r in RECEIPTS.values() for line in r.line_items}
    assert not {t.lower() for t, _ in default_lookup().library} & own


def test_every_trace_line_says_who_decided_it():
    import json
    from validator.data import ROOT
    lines = [x for row in (ROOT / "outputs" / "traces.jsonl").read_text().splitlines() for x in json.loads(row)["classify"]["lines"]]
    assert all("decided_by" in x for x in lines)
    assert {x["decided_by"] for x in lines} <= {"lookup (memory)", "lookup (library)", "model", "model (code check)", "code (not itemized)"}


@pytest.mark.parametrize("a, b, same", [
    ("ES TEH MANIS", "teh manis", True),
    ("EL KEUN HWANGTAE", "EL KEUN HWANGTAE SUNDUBU (TUKBEGI)", True),
    ("*Rhum", "BANANA RHUM RAISIN", False),  # one shared word isn't the same item
    ("Grilled salmon entree", "Chicken Steak", False),
    ("Large 1", "Grande", False),
    ("25%", "25%", False),  # no words at all
])
def test_the_same_item_word_test(a, b, same):
    from validator.lookup import same_words
    assert same_words(a, b, 0.5) == same


def test_two_key_learning_remembers_agreed_lines_and_keeps_restricted_ones_as_risk():
    lookup = Lookup()
    lookup._load_vectors()
    if lookup.ready is not True:
        pytest.skip("the committed vectors don't cover the library")
    memory, risk = len(lookup.memory), len(lookup.risk)
    assert lookup.learn("Iced tea", "food_beverage", False, "food_beverage", 0.99).startswith("left out: unsure")
    assert lookup.learn("Pepperoni pizza", "food_beverage", True, "other", 0.99).startswith("left out: the model disagrees")
    assert (len(lookup.memory), len(lookup.risk)) == (memory, risk)
