"""Run experiment 9: `make semantic-v31` (uv run --group semantic python -m experiments.semantic_v31).

Calibrates the same-item threshold on the 600 receipts already used, then streams 200 fresh CORD receipts
(R400–R599) through the pipeline's lookup twice, as it is (V3) and with the stricter same-item rule (V3.1), both
learning after every 50, against the model on every line (M). Also runs V3.1 on the project's own sets.
Everything goes through validator/lookup.py and validator/classifier.py. Writes results/.
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
from experiments.semantic_first.__main__ import receipts_score, records
from experiments.semantic_v2.__main__ import cord_score
from validator.classifier import classify
from validator.data import ROOT, load_receipts
from validator.llm import CACHE_DIR, LLM, LLMError, ledger_total
from validator.lookup import TAXONOMY, Embedder, Lookup, same_words
from validator.normaliser import normalise
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS, LEDGER = HERE / "results", HERE / "spend.jsonl"
BATCH, OVERLAP = 50, 0.5


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER)


def embedder(config: dict) -> Embedder:
    return Embedder(config["model"], HERE / "cache" / "vectors.npz")


# Settings the pipeline gained after experiment 8. Each arm starts from experiment 8's lookup (V3) plus its own
# overrides, so the experiments replay the same arms whatever rules/lookup/config.yaml now says.
PIPELINE_ONLY = {"memory_rule", "tau_word", "block_tau", "mode"}


def lookup(overrides: dict, emb: Embedder) -> Lookup:
    lk = Lookup()
    lk.config = {**{k: v for k, v in lk.config.items() if k not in PIPELINE_ONLY}, "mode": "decide", **overrides}
    lk.embedder = emb
    lk._load_vectors()
    return lk


def calibrate(lines: list[dict], vectors: np.ndarray) -> tuple[float, dict]:
    """τ_same: the lowest similarity at which every same-word pair of sure labels on different receipts agrees on category."""
    sims = vectors @ vectors.T
    pairs = []
    for i in range(len(lines)):
        for j in np.nonzero(sims[i, i + 1:] >= 0.5)[0] + i + 1:
            if lines[i]["receipt"] != lines[j]["receipt"] and same_words(lines[i]["text"], lines[j]["text"], OVERLAP):
                pairs.append((float(sims[i, j]), lines[i]["category"] == lines[j]["category"], i, int(j)))
    pairs.sort(reverse=True)
    worst = max((s for s, same, _, _ in pairs if not same), default=None)
    above = [s for s, *_ in pairs if worst is None or s > worst]
    tau = min(above) if above else 1.01
    disagreeing = [(round(s, 4), lines[i]["text"], lines[i]["category"], lines[j]["text"], lines[j]["category"])
                   for s, same, i, j in pairs if not same][:8]
    return tau, {"same_word_pairs": len(pairs), "pairs_at_or_above": len(above), "highest_disagreeing_pair": worst,
                 "examples_of_disagreeing_pairs": disagreeing}


def stream(name: str, lk: Lookup, fresh, labels, glosses, llm, m) -> tuple[list, list[dict], dict]:
    classified, rows, learned = [], [], {}
    for start in range(0, len(fresh), BATCH):
        batch = fresh[start:start + BATCH]
        for r in batch:
            c = classify(r, glosses, llm, lookup=lk)
            classified.append(c.receipt)
            for line in r.line_items:
                who = c.decided_by.get(line.line_id, {})
                item, label = c.receipt.items[line.line_id], labels[line.line_id]
                rows.append({"arm": name, "batch": start // BATCH + 1, "receipt": r.receipt_id, "line_id": line.line_id, "text": line.description_raw,
                             "label": label["category"], "label_sure": label["sure"], "decided_by": who.get("decided_by", "model"), "why": who.get("why", ""),
                             "match": who["nearest"][0][0] if who.get("nearest") else "", "category": item.category,
                             "M": m[r.receipt_id].receipt.items[line.line_id].category})
        for r in batch:
            for line in r.line_items:
                mi, label = m[r.receipt_id].receipt.items[line.line_id], labels[line.line_id]
                outcome = lk.learn(line.description_raw, label["category"], label["sure"], mi.category, mi.confidence)
                learned[outcome] = learned.get(outcome, 0) + 1
    return classified, rows, learned


def lookup_stats(rows: list[dict]) -> dict:
    decided = [r for r in rows if r["decided_by"].startswith("lookup")]
    sure = [r for r in decided if r["label_sure"]]
    memory = [r for r in decided if r["decided_by"] == "lookup (memory)"]
    return {"lines": len(rows), "decided": len(decided), "share": round(len(decided) / max(1, len(rows)), 4),
            "by_memory": len(memory), "by_library": len(decided) - len(memory),
            "right_sure": sum(r["category"] == r["label"] for r in sure), "decided_sure": len(sure),
            "restricted_as_safe": [r["text"] for r in decided if TAXONOMY[r["label"]] and r["label_sure"]],
            "wrong": [{k: r[k] for k in ("text", "category", "label", "label_sure", "match")} for r in decided if r["category"] != r["label"]],
            "memory_matches_failing_the_word_test": sum(not same_words(r["text"], r["match"], OVERLAP) for r in memory),
            "receipts_no_model_call": None}


def main() -> int:
    gold, variants = load_gold(), load_variants()
    main_receipts = load_receipts()
    dev_receipts, expected = load_dev()
    reworded = reword(main_receipts, variants)
    seen = [(load_receipts(cord.RECEIPTS_PATH), cord.load_labels()),
            (load_receipts(ROOT / "cord" / "test" / "receipts.jsonl"), cord.load_labels(ROOT / "cord" / "test" / "labels.jsonl")),
            (load_receipts(ROOT / "cord" / "train" / "receipts.jsonl"), cord.load_labels(ROOT / "cord" / "train" / "labels.jsonl"))]
    fresh, labels = load_receipts(ROOT / "cord" / "train2" / "receipts.jsonl"), cord.load_labels(ROOT / "cord" / "train2" / "labels.jsonl")

    base = Lookup()
    emb = embedder(base.config)
    calib = [{"receipt": r.receipt_id, "text": line.description_raw, "category": lab[line.line_id]["category"]}
             for rs, lab in seen for r in rs for line in r.line_items if lab[line.line_id]["sure"]]
    vectors = np.stack(emb.embed([normalise(c["text"]) for c in calib]))
    tau_same, calib_info = calibrate(calib, vectors)
    v31 = {"memory_rule": "same_item", "tau_same": round(tau_same, 6), "word_overlap": OVERLAP}
    print(f"tau_same {tau_same:.4f}: {calib_info['pairs_at_or_above']} of {calib_info['same_word_pairs']} same-word pairs at or above; "
          f"highest disagreeing pair {calib_info['highest_disagreeing_pair']}")

    llm = client()
    glosses = translate_all(fresh, llm)
    m = {r.receipt_id: classify(r, glosses, llm) for r in fresh}
    m_score = cord_score([m[r.receipt_id].receipt for r in fresh], fresh, labels)
    arms, rows = {}, []
    for name, overrides in (("V3", {}), ("V3.1", v31)):
        classified, arm_rows, learned = stream(name, lookup(overrides, emb), fresh, labels, glosses, llm, m)
        stats = lookup_stats(arm_rows)
        stats["receipts_no_model_call"] = sum(all(r["decided_by"].startswith("lookup") for r in arm_rows if r["receipt"] == rec.receipt_id)
                                              for rec in fresh if rec.line_items)
        arms[name] = {"score": cord_score(classified, fresh, labels), "lookup": stats, "learning": learned}
        rows += arm_rows

    # V3.1 on the project's own sets, with the memory as committed (no stream learning).
    own_lk = lookup(v31, emb)
    timing = sorted(rid for rid in gold if not in_scope(rid))
    own = {}
    for arm, lk in (("M", None), ("V3.1", own_lk)):
        g = translate_all(main_receipts + dev_receipts + reworded, llm)
        got = records([classify(r, g, llm, lookup=lk).receipt for r in main_receipts])
        dev_rows = score_dev([SimpleNamespace(receipt=classify(r, g, llm, lookup=lk).receipt) for r in dev_receipts], expected)
        timing_context = [classify(r, g, llm, lookup=lk).receipt for r in main_receipts if r.receipt_id in timing]
        rw = records([classify(r, g, llm, lookup=lk).receipt for r in reworded] + timing_context)
        own[arm] = {"receipts": receipts_score(got, gold), "timing": {rid: receipt_matches(got[rid], gold[rid]) for rid in timing},
                    "dev": {"category": sum(r.category_ok for r in dev_rows), "item_kind": sum(r.kind_ok for r in dev_rows), "recipient": sum(r.recipient_ok for r in dev_rows)},
                    "reworded": receipts_score(rw, gold)}
    own_decided = sum(d.decided for r in main_receipts for d in own_lk.decide_receipt(r).values())

    with tempfile.TemporaryDirectory() as tmp:
        texts = list(dict.fromkeys(normalise(line.description_raw) for r in fresh for line in r.line_items))
        (Path(tmp) / "t.json").write_text(json.dumps(texts), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "experiments.semantic_v2.embed", "--recompute", str(Path(tmp) / "a.npy"), str(Path(tmp) / "t.json")],
                       check=True, cwd=ROOT, capture_output=True)
        diff = float(np.abs(np.load(Path(tmp) / "a.npy") - np.stack(emb.embed(texts))).max())

    V, M_own, V_own = arms["V3.1"], own["M"], own["V3.1"]
    bar = {
        "1 own sets: 28/28, 65/65, 0 FP, 0 missed; timing 4/4; dev ≥42; reworded ≥ M":
            V_own["receipts"]["exact"] == 28 and V_own["receipts"]["items"] == 65 and V_own["receipts"]["false_positives"] == 0
            and V_own["receipts"]["missed"] == 0 and all(V_own["timing"].values()) and min(V_own["dev"].values()) >= 42
            and V_own["reworded"]["items"] >= M_own["reworded"]["items"] and V_own["reworded"]["exact"] >= M_own["reworded"]["exact"],
        "2 safe: no restricted item called safe; ≥99.5% right on sure labels":
            not V["lookup"]["restricted_as_safe"] and (not V["lookup"]["decided_sure"] or V["lookup"]["right_sure"] / V["lookup"]["decided_sure"] >= 0.995),
        "3 as accurate as M": V["score"]["lines_right"] >= m_score["lines_right"] - 2 and V["score"]["false_alarms"] <= m_score["false_alarms"]
            and V["score"]["missed"] <= m_score["missed"],
        "4 useful: ≥35% of fresh lines": V["lookup"]["share"] >= 0.35,
        "5 repeatable": diff <= 1e-5,
    }
    metrics = {"tau_same": tau_same, "calibration": calib_info, "M": m_score, "arms": arms,
               "own_sets": {**own, "V3.1_lines_decided_on_32": own_decided}, "repeatability": {"largest_difference": diff},
               "bar": bar, "verdict": "passes" if all(bar.values()) else "fails: " + "; ".join(k for k, ok in bar.items() if not ok),
               "spend_usd": ledger_total(LEDGER)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    with (RESULTS / "stream.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("M:", m_score)
    for name, a in arms.items():
        print(f"{name}: {a['score']}\n   lookup {json.dumps({k: v for k, v in a['lookup'].items() if k != 'wrong'})}\n   wrong {a['lookup']['wrong']}\n   learning {a['learning']}")
    print("own sets:", {k: (v["receipts"]["exact"], v["receipts"]["items"], v["dev"], v["reworded"]["items"], v["reworded"]["exact"]) for k, v in own.items()},
          "V3.1 decided on the 32:", own_decided)
    print(f"repeatability {diff}; verdict: {metrics['verdict']}; spend ${metrics['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
