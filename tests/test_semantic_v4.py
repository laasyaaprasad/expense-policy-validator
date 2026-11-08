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


def test_in_shadow_mode_the_lookup_decides_nothing_and_records_soup_of_the_day():
    from validator.lookup import default_lookup
    assert default_lookup().config["mode"] == "shadow"
    decisions = {lid: d for r in load_receipts() for lid, d in default_lookup().decide_receipt(r).items()}
    assert not any(d.decided for d in decisions.values())
    shadow = {lid: d.shadow for lid, d in decisions.items() if d.shadow}
    assert shadow == {"RCP-021-L2": "food_beverage"}
    assert decisions["RCP-021-L2"].trace() | {"why": "", "nearest": []} == {"decided_by": "model", "why": "", "nearest": [], "shadow": "food_beverage"}
    deciding = Lookup(overrides={"mode": "decide"})
    assert [lid for r in load_receipts() for lid, d in deciding.decide_receipt(r).items() if d.decided] == ["RCP-021-L2"]


def test_shadow_mode_gives_exactly_the_models_answers_on_the_32_receipts():
    from validator.classifier import classify
    from validator.llm import CACHE_DIR, LLM
    from validator.lookup import default_lookup
    from validator.pipeline import needs_model
    from validator.translate import translate_all
    llm = LLM.offline(cache_dir=CACHE_DIR, ledger_path=ROOT / "outputs" / "never-written.jsonl")
    receipts = load_receipts()
    assert needs_model(receipts, default_lookup()) == needs_model(receipts, None)
    glosses = translate_all(receipts, llm)
    for r in receipts:
        shadow, alone = classify(r, glosses, llm, lookup=default_lookup()), classify(r, glosses, llm)
        assert shadow.receipt.items == alone.receipt.items, r.receipt_id
    assert not (ROOT / "outputs" / "never-written.jsonl").exists()


def test_the_shadow_report_is_not_ready_to_decide_without_a_clients_reviews():
    report = json.loads((ROOT / "outputs" / "shadow.json").read_text())
    assert report["mode"] == "shadow" and report["ready_to_decide"] is False
    cord = report["sets"]["CORD validation"]
    assert (cord["would_decide"], cord["agree_with_label"], cord["restricted_called_safe"]) == (80, 80, [])
    assert {d["model"] for d in cord["disagree_with_model"]} == {"other"} and all(d["lookup"] == d["label"] for d in cord["disagree_with_model"])


def test_recalibration_refuses_too_few_reviewed_lines(tmp_path, capsys):
    import shutil
    from validator.calibrate import main
    from validator.lookup import LOOKUP_DIR
    shutil.copy(LOOKUP_DIR / "config.yaml", tmp_path / "config.yaml")
    reviewed = tmp_path / "reviewed.jsonl"
    reviewed.write_text("".join(json.dumps({"receipt_id": f"R{i}", "text": "Nasi goreng", "category": "food_beverage"}) + "\n" for i in range(50)))
    assert main(["--reviewed", str(reviewed), "--directory", str(tmp_path)]) == 2
    assert "refused: 50 sure reviewed lines" in capsys.readouterr().out
    assert (tmp_path / "config.yaml").read_text() == (LOOKUP_DIR / "config.yaml").read_text() and not (tmp_path / "calibration.json").exists()


def test_recalibration_finds_the_lowest_threshold_with_999_agreement():
    import numpy as np
    from validator.calibrate import calibrate
    # Hand-set vectors: "tea" and "teh" are near (0.96); "wine" sits 0.95 from "tea", above the floor.
    v = {"tea": [1, 0, 0], "teh": [0.96, 0.28, 0], "wine": [0.95, 0, 0.312], "es": [0, 1, 0]}
    unit = {k: np.array(x, dtype=np.float32) / np.linalg.norm(x) for k, x in v.items()}
    embed = lambda texts: [unit.get(t.lower(), unit["tea"]) for t in texts]  # every sentence is alike, so every pair is a candidate
    lines = [{"receipt_id": "A", "text": "Tea", "category": "food_beverage"}, {"receipt_id": "B", "text": "Teh", "category": "food_beverage"},
             {"receipt_id": "C", "text": "Wine", "category": "alcohol"}]
    tau, info = calibrate(lines, embed, 0.9)
    # tea~teh agree (0.96); tea~wine and teh~wine are restricted disagreements, so τ must sit above them
    assert info["highest_restricted_disagreement"] >= 0.94 and tau == pytest.approx(0.96, abs=1e-4)
