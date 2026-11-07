"""Run experiment 7: `make semantic-v2` (uv run --group semantic python -m experiments.semantic_v2).

Main's classifier (M) against the semantic lookup first (V2), and V2 after learning from CORD validation's
labels (V2+L), on every test set (PLAN.md). The lookup is local and cached in cache/vectors.npz; the model
calls replay the repository's cache, and a miss needs OPENROUTER_API_KEY. Writes results/.
"""

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from evaluation import cord
from evaluation.dev import load_dev, score as score_dev
from evaluation.evaluate import receipt_matches
from evaluation.gold import in_scope, load_gold
from experiments.paraphrase.__main__ import load_variants, reword
from experiments.semantic_first.__main__ import combine, receipts_score, records
from experiments.semantic_first.semantic import TAXONOMY, Anchor, decontaminate
from experiments.semantic_v2.embed import Embedder
from experiments.semantic_v2.lookup import Decision, Lookup, load_library
from validator.classifier import Classification, classify
from validator.data import ROOT, ReceiptInput, load_receipts
from validator.llm import CACHE_DIR, LLM, LLMError, ledger_total
from validator.models import ClassifiedReceipt
from validator.normaliser import normalise
from validator.pipeline import CONFIDENCE_THRESHOLD, decide_all
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS, LEDGER = HERE / "results", HERE / "spend.jsonl"
CORD_TEST = ROOT / "cord" / "test"
REPEAT_TOLERANCE, USEFUL_SHARE, SAFE_ACCURACY = 1e-5, 0.20, 0.99


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER)


def itemized_lines(receipts: list[ReceiptInput]) -> list[tuple[ReceiptInput, object]]:
    return [(r, line) for r in receipts if r.itemized for line in r.line_items]


def learned_library(library: list[Anchor], labels: dict[str, dict]) -> list[Anchor]:
    """The library plus CORD validation's sure labels, one line per text; a text labelled two ways is left out."""
    seen: dict[str, set[str]] = {}
    for row in labels.values():
        if row["sure"]:
            seen.setdefault(normalise(row["description_raw"]).lower(), set()).add(row["category"])
    have = {normalise(a.text).lower() for a in library}
    added = [Anchor(next(iter(cats)), "id", text, "reviewed") for text, cats in sorted(seen.items()) if len(cats) == 1 and text not in have]
    return library + added


def cord_score(classified: list[ClassifiedReceipt], receipts: list[ReceiptInput], labels: dict[str, dict]) -> dict:
    system = {line_id: item.model_dump() for c in classified for line_id, item in c.items.items()}
    items = cord.score_items(system, labels)
    ours = cord.dispositions(decide_all(classified)[1])
    want = cord.dispositions(decide_all(cord.labelled(receipts, labels))[1])
    r = cord.score_receipts(ours, want)
    return {"lines_right": items["all"]["category_correct"]["k"], "lines": items["all"]["items"],
            "sure_lines_right": items["sure"]["category_correct"]["k"], "sure_lines": items["sure"]["items"],
            "false_alarms": r["false_alarms"]["k"], "false_flags": r["false_flags"]["k"], "false_reviews": r["false_reviews"]["k"],
            "clean": r["false_alarms"]["n"], "missed": r["missed_flags"]["k"], "violations": r["missed_flags"]["n"],
            "flags": r["flag_precision"]["n"], "flags_right": r["flag_precision"]["k"]}


def repeatability(texts: list[str], embedder: Embedder) -> dict:
    """Embed every text again in a fresh process and compare with the vectors this run used (bar 8)."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        previous = RESULTS / "metrics.json"
        if previous.exists():
            return {**json.loads(previous.read_text())["repeatability"], "note": "from the committed run: the model isn't installed here"}
        return {"checked": False, "note": "the model isn't installed; run with --group semantic"}
    with tempfile.TemporaryDirectory() as tmp:
        texts_path, out = Path(tmp) / "texts.json", Path(tmp) / "again.npy"
        texts_path.write_text(json.dumps(texts), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "experiments.semantic_v2.embed", "--recompute", str(out), str(texts_path)],
                       check=True, cwd=ROOT, capture_output=True)
        diff = float(np.abs(np.load(out) - embedder.embed(texts)).max())
    return {"checked": True, "texts": len(texts), "largest_difference": diff, "identical_within_tolerance": diff <= REPEAT_TOLERANCE}


def main() -> int:
    gold, variants = load_gold(), load_variants()
    main_receipts = load_receipts()
    dev_receipts, expected = load_dev()
    reworded = reword(main_receipts, variants)
    val_receipts, val_labels = load_receipts(cord.RECEIPTS_PATH), cord.load_labels()
    test_receipts, test_labels = load_receipts(CORD_TEST / "receipts.jsonl"), cord.load_labels(CORD_TEST / "labels.jsonl")
    sets = {"receipts": main_receipts, "dev": dev_receipts, "reworded": reworded, "cord": val_receipts, "cord_test": test_receipts}
    truth = {**{line_id: (item.category, True) for g in gold.values() for line_id, item in g.items.items()},
             **{line_id: (e["category"], True) for line_id, e in expected.items()},
             **{line_id: (row["category"], row["sure"]) for line_id, row in {**val_labels, **test_labels}.items()}}

    # Step 1's library: decontaminated against every test line before anything is embedded.
    test_texts = [line.description_raw for rs in sets.values() for r in rs for line in r.line_items]
    library, dropped = decontaminate(load_library(), test_texts)
    learned = learned_library(library, val_labels)
    embedder = Embedder()
    lines = {name: itemized_lines(rs) for name, rs in sets.items()}
    line_texts = {name: [normalise(line.description_raw) for _, line in ls] for name, ls in lines.items()}
    every_text = list(dict.fromkeys([normalise(a.text) for a in learned] + [t for ts in line_texts.values() for t in ts]))
    embedder.embed(every_text)
    lookup = Lookup(library, embedder.embed([normalise(a.text) for a in library]))
    lookup_learned = Lookup(learned, embedder.embed([normalise(a.text) for a in learned]))
    print(f"library {len(library)} kept, {len(dropped)} dropped; cutoffs {lookup.cutoff}; learned library {len(learned)}, cutoffs {lookup_learned.cutoff}")

    def run_lookup(lk: Lookup, name: str) -> dict[str, Decision]:
        ls = lines[name]
        return dict(zip([line.line_id for _, line in ls], lk.decide(line_texts[name], embedder.embed(line_texts[name]), [r.mcc for r, _ in ls])))

    semantic = {name: run_lookup(lookup, name) for name in sets}
    semantic_learned = run_lookup(lookup_learned, "cord_test")

    # M: main's classifier on every set (the repository's cache, fresh calls on a miss).
    llm = client()
    m: dict[str, dict[str, Classification]] = {}
    for name, rs in sets.items():
        glosses = translate_all(rs, llm)
        m[name] = {r.receipt_id: classify(r, glosses, llm) for r in rs}
        print(f"M classified {name}")

    arms = {"M": {name: [m[name][r.receipt_id].receipt for r in rs] for name, rs in sets.items()},
            "V2": {name: [combine(m[name][r.receipt_id], semantic[name]) for r in rs] for name, rs in sets.items()}}
    arms["V2+L"] = {"cord_test": [combine(m["cord_test"][r.receipt_id], semantic_learned) for r in test_receipts]}
    timing = sorted(rid for rid in gold if not in_scope(rid))

    results = {}
    for arm, by_set in arms.items():
        out = {}
        if "receipts" in by_set:
            got = records(by_set["receipts"])
            out["receipts"] = receipts_score(got, gold)
            out["timing"] = {rid: receipt_matches(got[rid], gold[rid]) for rid in timing}
            rows = score_dev([SimpleNamespace(receipt=c) for c in by_set["dev"]], expected)
            out["dev"] = {"items": len(rows), "category": sum(r.category_ok for r in rows), "item_kind": sum(r.kind_ok for r in rows),
                          "recipient": sum(r.recipient_ok for r in rows),
                          "wrong": [{"line_id": r.line_id, "text": r.text, "got": r.got, "want": r.expected} for r in rows if not r.category_ok]}
            timing_context = [c for c in by_set["receipts"] if c.receipt.receipt_id in timing]
            out["reworded"] = receipts_score(records(by_set["reworded"] + timing_context), gold)
            out["cord"] = cord_score(by_set["cord"], val_receipts, val_labels)
        out["cord_test"] = cord_score(by_set["cord_test"], test_receipts, test_labels)
        results[arm] = out

    # The lookup on its own: what it decided, and whether it was right.
    def decided_rows(decisions: dict[str, dict[str, Decision]]) -> list[dict]:
        return [{"set": name, "line_id": line_id, "got": d.category, "want": truth[line_id][0], "sure": truth[line_id][1]}
                for name, ds in decisions.items() for line_id, d in ds.items() if d.decided]

    def safety(rows: list[dict]) -> dict:
        sure = [r for r in rows if r["sure"]]
        wrong = [r for r in sure if r["got"] != r["want"]]
        return {"decided": len(rows), "decided_sure": len(sure), "right_sure": len(sure) - len(wrong),
                "wrong_sure": wrong, "restricted_errors_sure": [r for r in wrong if TAXONOMY[r["got"]] != TAXONOMY[r["want"]]],
                "wrong_unsure": [r for r in rows if not r["sure"] and r["got"] != r["want"]]}

    v2_safety, learned_safety = safety(decided_rows(semantic)), safety(decided_rows({"cord_test": semantic_learned}))
    cord_lines = len(lines["cord"]) + len(lines["cord_test"])
    cord_decided = sum(d.decided for name in ("cord", "cord_test") for d in semantic[name].values())
    why = {}
    for ds in semantic.values():
        for d in ds.values():
            reason = "decided" if d.decided else ("margin below the cutoff" if d.why.startswith("margin") else d.why.split(":")[0] + (
                ": " + d.why.split(":")[1].split(" (")[0].strip() if d.why.startswith("code check") else ""))
            why[reason] = why.get(reason, 0) + 1
    no_call = {name: sum(all(semantic[name][line.line_id].decided for line in r.line_items) for r in rs if r.itemized) for name, rs in sets.items()}
    no_call_learned = sum(all(semantic_learned[line.line_id].decided for line in r.line_items) for r in test_receipts if r.itemized)
    repeat = repeatability(every_text, embedder)

    M, V = results["M"], results["V2"]
    bar = {
        "1 receipts 28/28, 65/65, 0 FP, 0 missed": V["receipts"]["exact"] == 28 and V["receipts"]["items"] == 65
                                                   and V["receipts"]["false_positives"] == 0 and V["receipts"]["missed"] == 0,
        "2 timing 4/4": all(V["timing"].values()),
        "3 dev ≥42 category, kind, recipient": min(V["dev"]["category"], V["dev"]["item_kind"], V["dev"]["recipient"]) >= 42,
        "4 reworded no worse than M": V["reworded"]["items"] >= M["reworded"]["items"] and V["reworded"]["exact"] >= M["reworded"]["exact"],
        **{f"5 {name}: lines ≥ M−2, alarms ≤ M, missed ≤ M": V[name]["lines_right"] >= M[name]["lines_right"] - 2
           and V[name]["false_alarms"] <= M[name]["false_alarms"] and V[name]["missed"] <= M[name]["missed"] for name in ("cord", "cord_test")},
        "6 safe: ≥99% right on sure labels, no restricted error": (not v2_safety["decided_sure"] or v2_safety["right_sure"] / v2_safety["decided_sure"] >= SAFE_ACCURACY)
                                                                 and not v2_safety["restricted_errors_sure"],
        "7 useful: ≥20% of CORD lines": cord_decided >= USEFUL_SHARE * cord_lines,
        "8 repeatable": bool(repeat.get("identical_within_tolerance")),
    }
    metrics = {
        "library": {"written": len(load_library()), "kept": len(library), "dropped": dropped, "learned": len(learned)},
        "lookup": {**lookup.summary(), "decided_per_set": {name: f"{sum(d.decided for d in ds.values())} of {len(ds)}" for name, ds in semantic.items()},
                   "cord_decided": f"{cord_decided} of {cord_lines}", "why_lines_went_to_the_model": why,
                   "receipts_needing_no_model_call": no_call, "safety": v2_safety},
        "learning": {**lookup_learned.summary(), "cord_test_decided": f"{sum(d.decided for d in semantic_learned.values())} of {len(semantic_learned)}",
                     "receipts_needing_no_model_call": no_call_learned, "safety": learned_safety},
        "arms": results,
        "repeatability": repeat,
        "bar": bar,
        "verdict": "passes" if all(bar.values()) else "fails: " + "; ".join(k for k, ok in bar.items() if not ok),
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "spend_usd": ledger_total(LEDGER),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    write_lines(sets, semantic, semantic_learned, arms, truth, expected)
    print_summary(metrics)
    return 0


def write_lines(sets, semantic, semantic_learned, arms, truth, expected) -> None:
    with (RESULTS / "lines.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["set", "line_id", "text", "trap", "want", "label_sure", "decided_by", "why", "lookup_best", "margin",
                    "nearest", "M", "M_confidence", "V2", "V2_confidence", "M_right", "V2_right", "V2L_decided_by", "V2L"])
        for name, rs in sets.items():
            m_items = {line_id: item for c in arms["M"][name] for line_id, item in c.items.items()}
            v_items = {line_id: item for c in arms["V2"][name] for line_id, item in c.items.items()}
            l_items = {line_id: item for c in arms["V2+L"].get(name, []) for line_id, item in c.items.items()}
            for r in rs:
                for line in r.line_items:
                    d, lid = semantic[name].get(line.line_id), line.line_id
                    want, sure = truth.get(lid, ("", ""))
                    by = "not itemized (code)" if d is None else "lookup" if d.decided else ("code check" if d.why.startswith(("code check", "text gate")) else "model")
                    ld = semantic_learned.get(lid)
                    w.writerow([name, lid, line.description_raw, (expected.get(lid) or {}).get("trap") or "", want, sure, by,
                                d.why if d else "", d.category if d else "", f"{d.margin:.4f}" if d else "",
                                " | ".join(f"{t} ({c}, {s:.3f})" for t, c, s in d.neighbours) if d else "",
                                m_items[lid].category, f"{m_items[lid].confidence:.4f}", v_items[lid].category, f"{v_items[lid].confidence:.4f}",
                                m_items[lid].category == want, v_items[lid].category == want,
                                ("lookup" if ld.decided else "model") if ld else "", l_items[lid].category if lid in l_items else ""])


def print_summary(m: dict) -> None:
    lk, le = m["lookup"], m["learning"]
    print(f"lookup: cutoffs {lk['cutoff']}; decided {lk['decided_per_set']}; CORD {lk['cord_decided']}; "
          f"safety {lk['safety']['right_sure']}/{lk['safety']['decided_sure']} sure right, restricted errors {len(lk['safety']['restricted_errors_sure'])}")
    print(f"why lines went to the model: {lk['why_lines_went_to_the_model']}")
    print(f"receipts with no model call: {lk['receipts_needing_no_model_call']}; after learning, CORD test {le['cord_test_decided']} decided, "
          f"{le['receipts_needing_no_model_call']} receipts with no call, safety {le['safety']['right_sure']}/{le['safety']['decided_sure']}")
    for arm, r in m["arms"].items():
        parts = []
        if "receipts" in r:
            parts.append(f"28: {r['receipts']['exact']}/28 exact, {r['receipts']['items']}/65 items, FP {r['receipts']['false_positives']}, missed {r['receipts']['missed']}; "
                         f"timing {sum(r['timing'].values())}/4; dev {r['dev']['category']}/43; reworded {r['reworded']['items']}/65, {r['reworded']['exact']}/28")
            parts.append("CORD val {lines_right}/{lines}, alarms {false_alarms}/{clean}, missed {missed}/{violations}".format(**r["cord"]))
        parts.append("CORD test {lines_right}/{lines}, alarms {false_alarms}/{clean}, missed {missed}/{violations}".format(**r["cord_test"]))
        print(f"{arm}: " + "; ".join(parts))
    print(f"repeatability: {m['repeatability']}")
    print(f"verdict: {m['verdict']}; spend ${m['spend_usd']:.4f}")


if __name__ == "__main__":
    sys.exit(main())
