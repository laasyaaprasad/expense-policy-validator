"""Experiment 5 (experiments/paraphrase/PLAN.md): the reworded lines are well formed, the run refuses
unreviewed variants, and its scoring reproduces the committed result when nothing is reworded."""

import csv
import re

import pytest

from evaluation.blame import load_classified
from evaluation.evaluate import score_items, score_receipts
from evaluation.gold import in_scope, load_gold
from experiments.paraphrase.__main__ import REVIEW, load_variants, records, reword
from validator.data import load_receipts

RECEIPTS = load_receipts()
VARIANTS = load_variants(require_review=False)
NAMES = re.compile(r"\b[A-Z]\. [A-Z][a-z]+")


def test_every_itemized_scored_line_has_one_variant():
    want = {line.line_id for r in RECEIPTS if in_scope(r.receipt_id) and r.itemized for line in r.line_items}
    assert set(VARIANTS) == want and len(want) == 64


def test_each_variant_is_new_text_of_a_known_kind_and_keeps_recipient_names():
    lines = {line.line_id: line for r in RECEIPTS for line in r.line_items}
    for line_id, row in VARIANTS.items():
        original = lines[line_id]
        assert row["original"] == original.description_raw and row["language"] == original.language
        assert row["variant"].strip() and row["variant"].casefold() != row["original"].casefold(), line_id
        assert row["kind"] in {"paraphrase", "same trap"}, line_id
        assert set(NAMES.findall(row["original"])) <= set(NAMES.findall(row["variant"])), line_id


def test_the_run_refuses_variants_nobody_has_checked(tmp_path):
    with REVIEW.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    path = tmp_path / "review.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([{**row, "ok": "yes"} for row in rows[:-1]] + [{**rows[-1], "ok": ""}])
    with pytest.raises(SystemExit, match="1 of 64"):
        load_variants(path)


def test_rewording_changes_only_the_text_of_scored_lines():
    reworded = {r.receipt_id: r for r in reword(RECEIPTS, VARIANTS)}
    assert sorted(reworded) == sorted(r.receipt_id for r in RECEIPTS if in_scope(r.receipt_id))
    for original in RECEIPTS:
        if original.receipt_id not in reworded:
            continue
        new = reworded[original.receipt_id]
        assert new.model_dump(exclude={"line_items"}) == original.model_dump(exclude={"line_items"})
        for a, b in zip(original.line_items, new.line_items):
            assert a.model_dump(exclude={"description_raw"}) == b.model_dump(exclude={"description_raw"})
            assert b.description_raw == (VARIANTS[a.line_id]["variant"] if a.line_id in VARIANTS else a.description_raw)


def test_scoring_reproduces_the_committed_result_when_nothing_is_reworded():
    gold, got = load_gold(), records(load_classified())
    assert score_items(got, gold)["accuracy"]["k"] == 65
    assert score_receipts(got, gold)["exact_match"]["k"] == 28


def test_the_write_up_states_the_committed_numbers():
    import json
    from experiments.paraphrase.__main__ import HERE
    m = json.loads((HERE / "results" / "metrics.json").read_text(encoding="utf-8"))
    page = (HERE / "README.md").read_text(encoding="utf-8")
    assert f"{m['items']['reworded']['k']} / 65" in page and f"{m['receipts']['reworded']['k']} / 28" in page
    assert f"${m['spend_usd']:.4f}" in page
