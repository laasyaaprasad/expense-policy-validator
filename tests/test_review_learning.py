"""People's reviews and `make learn` (validator/reviews.py, validator/learn.py, viewer/review.py). Offline: every test works
on a copy of rules/lookup in a temporary folder, with the committed vectors, and never touches the real files."""

import csv
import hashlib
import json
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from validator.data import ROOT, load_receipts
from validator.learn import learn
from validator.lookup import LOOKUP_DIR, Embedder, Lookup
from validator.reviews import REASONS, add_review, load_reviews, review_queue

RECEIPTS = {r.receipt_id: r for r in load_receipts()}
CLASSIFIED = {json.loads(x)["line_id"]: json.loads(x) for x in (ROOT / "outputs" / "classifications.jsonl").read_text().splitlines()}


def jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


@pytest.fixture
def lookup_dir(tmp_path):
    folder = tmp_path / "lookup"
    folder.mkdir()
    for name in ("config.yaml", "library.csv", "memory.csv", "vectors.npz"):
        shutil.copy(LOOKUP_DIR / name, folder / name)
    return folder


def item(line_id: str) -> dict:
    row = CLASSIFIED[line_id]
    return {"receipt_id": row["receipt_id"], "line_id": line_id, "text": row["description_raw"], "category": row["category"],
            "model_category": row["category"], "model_confidence": row["confidence"], "reasons": []}


def review(folder, line_id, category, sure=True, **model):
    return add_review(folder / "reviews.jsonl", {**item(line_id), **model}, category, sure, "Test Reviewer")


def digest(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def test_the_queue_holds_the_lines_worth_a_look_with_their_reasons():
    traces, results = jsonl(ROOT / "outputs" / "traces.jsonl"), jsonl(ROOT / "outputs" / "results.jsonl")
    texts = {line.line_id: line.description_raw for r in RECEIPTS.values() for line in r.line_items}
    queue = review_queue(traces, results, texts)
    cited = {lid for r in results if r["disposition"] in ("flag", "review") for e in r["exceptions"] for lid in e["line_ids"]}
    assert {q["line_id"] for q in queue} == cited and all(q["reasons"] for q in queue)
    assert len(review_queue(traces, results, texts, every_line=True)) == 79

    cord_traces, cord_results = jsonl(ROOT / "outputs" / "cord" / "traces.jsonl"), jsonl(ROOT / "outputs" / "cord" / "results.jsonl")
    reasons = {q["line_id"]: q["reasons"] for q in review_queue(cord_traces, cord_results, {})}
    assert REASONS[2] in reasons["CORD-V019-L2"]  # the lookup's audit sample
    assert all(REASONS[3] in reasons[k] for k in ("CORD-V053-L1", "CORD-V087-L3", "CORD-V092-L2"))  # lookup and AI disagree
    assert any(REASONS[0] in r for r in reasons.values())  # EXC-03


def test_a_review_needs_a_name_and_a_real_category(tmp_path):
    with pytest.raises(ValueError, match="name"):
        add_review(tmp_path / "r.jsonl", item("RCP-021-L1"), "food_beverage", True, "  ")
    with pytest.raises(ValueError, match="category"):
        add_review(tmp_path / "r.jsonl", item("RCP-021-L1"), "snacks", True, "Ana")
    record = add_review(tmp_path / "r.jsonl", item("RCP-021-L1"), "food_beverage", True, "Ana")
    assert load_reviews(tmp_path / "r.jsonl") == [record] and record["model_confidence"] == pytest.approx(0.999992)


def test_learning_end_to_end_a_reviewed_line_is_then_decided_by_the_lookup(lookup_dir):
    assert not Lookup(lookup_dir).decide_receipt(RECEIPTS["RCP-021"])["RCP-021-L1"].decided
    review(lookup_dir, "RCP-021-L1", "food_beverage")  # the AI said food at 0.99999: two keys
    code, records = learn(directory=lookup_dir)
    assert code == 0 and [r["outcome"] for r in records] == ["memory"]
    rows = list(csv.DictReader((lookup_dir / "memory.csv").open()))
    assert rows[-1] == {"role": "remember", "category": "food_beverage", "text": "Dinner entree - roast chicken", "reviewed_line": "RCP-021-L1"}
    d = Lookup(lookup_dir).decide_receipt(RECEIPTS["RCP-021"])["RCP-021-L1"]
    assert d.decided and d.category == "food_beverage"
    assert learn(directory=lookup_dir) == (0, [])  # applied once only


def test_the_two_key_rule_and_restricted_lines(lookup_dir):
    review(lookup_dir, "RCP-001-L2", "food_beverage", model_category="other", model_confidence=0.99)  # the AI disagrees
    review(lookup_dir, "RCP-021-L3", "food_beverage", model_confidence=0.6)  # the AI agrees but isn't sure
    review(lookup_dir, "RCP-004-L1", "food_beverage", sure=False)  # the person isn't sure
    review(lookup_dir, "RCP-010-L2", "alcohol")  # a restricted line: kept as a restricted reference
    _, records = learn(directory=lookup_dir)
    assert [r["outcome"].split(":")[0] for r in records] == ["left out", "left out", "left out", "risk"]
    assert "Draft IPA 16oz" in {t for t, _ in Lookup(lookup_dir).risk}


def test_a_correction_removes_a_wrong_memory_and_restricted_is_never_turned_safe(lookup_dir):
    assert Lookup(lookup_dir).decide_receipt(RECEIPTS["RCP-021"])["RCP-021-L2"].decided  # "Soup Of The Day" is remembered
    review(lookup_dir, "RCP-021-L2", "other")  # a person says otherwise; the AI said food, so it isn't learned either
    _, records = learn(directory=lookup_dir)
    assert records[0]["removed"] == [["Soup Of The Day", "food_beverage"]]
    assert not Lookup(lookup_dir).decide_receipt(RECEIPTS["RCP-021"])["RCP-021-L2"].decided  # back to the AI

    review(lookup_dir, "RCP-010-L2", "alcohol")
    learn(directory=lookup_dir)
    review(lookup_dir, "RCP-010-L2", "food_beverage", model_category="food_beverage", model_confidence=0.99)
    _, records = learn(directory=lookup_dir)
    assert records[0]["outcome"].startswith("left out: a person once confirmed this line restricted")


def test_a_dry_run_writes_nothing(lookup_dir):
    review(lookup_dir, "RCP-021-L1", "food_beverage")
    before = digest(lookup_dir)
    code, records = learn(directory=lookup_dir, dry_run=True)
    assert code == 0 and records[0]["outcome"] == "memory" and digest(lookup_dir) == before


def test_without_the_model_a_line_with_no_vector_is_refused_and_nothing_changes(lookup_dir, monkeypatch):
    monkeypatch.setattr(Embedder, "available", lambda self: False)
    add_review(lookup_dir / "reviews.jsonl", {**item("RCP-021-L1"), "text": "Zzyzx quantum gadget"}, "food_beverage", True, "Ana")
    before = digest(lookup_dir)
    code, _ = learn(directory=lookup_dir)
    assert code == 2 and digest(lookup_dir) == before


def test_each_client_has_its_own_memory_and_restricted_lines_are_shared(lookup_dir):
    acme = Lookup(lookup_dir, client="acme")
    assert acme.memory == [] and {t for t, _ in acme.risk} >= {t for t, _ in Lookup(lookup_dir).risk}
    assert not any(d.decided for d in acme.decide_receipt(RECEIPTS["RCP-021"]).values())  # a cold start: everything to the AI
    add_review(lookup_dir / "clients" / "acme" / "reviews.jsonl", item("RCP-010-L2"), "alcohol", True, "Ana", client="acme")
    add_review(lookup_dir / "clients" / "acme" / "reviews.jsonl", item("RCP-021-L1"), "food_beverage", True, "Ana", client="acme")
    assert learn("acme", lookup_dir)[0] == 0
    assert "Draft IPA 16oz" in {t for t, _ in Lookup(lookup_dir).risk}  # shared with the default client
    assert "Dinner entree - roast chicken" not in {t for t, _ in Lookup(lookup_dir).memory}  # safe memory stays with acme
    assert Lookup(lookup_dir, client="acme").decide_receipt(RECEIPTS["RCP-021"])["RCP-021-L1"].decided
    with pytest.raises(ValueError):
        Lookup(lookup_dir, client="../escape")


def test_the_review_page_saves_a_review_only_with_a_name(tmp_path, monkeypatch):
    path = tmp_path / "reviews.jsonl"
    monkeypatch.setenv("VALIDATOR_REVIEWS", str(path))
    at = AppTest.from_file(str(ROOT / "viewer" / "review.py"), default_timeout=60).run()
    assert not at.exception and len(at.dataframe[0].value) > 0
    save = next(b for b in at.button if b.label == "Save review")
    save.click().run()
    assert any("Enter your name" in e.value for e in at.error) and not path.exists()
    at.text_input[0].input("Ana")
    next(b for b in at.button if b.label == "Save review").click().run()
    [record] = load_reviews(path)
    assert record["reviewer"] == "Ana" and any("Saved" in s.value for s in at.success)


def test_the_committed_lookup_has_no_reviews_or_learned_lines():
    assert not (LOOKUP_DIR / "reviews.jsonl").exists() and not (LOOKUP_DIR / "learn_log.jsonl").exists()
    assert not (LOOKUP_DIR / "clients").exists()
