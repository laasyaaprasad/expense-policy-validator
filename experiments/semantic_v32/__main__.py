"""Run experiment 10: `make semantic-v32` (uv run --group semantic python -m experiments.semantic_v32).

Calibrates the same-item threshold with a 99.9% tolerance on the 800 receipts used so far, warms the memory up on
R400–R599, then streams the last 200 fresh CORD receipts (R600–R799) through the pipeline's lookup as it is (V3)
and with the same-item rule (V3.2), against the model on every line (M). Writes results/.
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
from experiments.semantic_v31.__main__ import OVERLAP, lookup, lookup_stats, stream
from validator.classifier import classify
from validator.data import ROOT, load_receipts
from validator.llm import CACHE_DIR, LLM, LLMError, ledger_total
from validator.lookup import TAXONOMY, Embedder, Lookup, same_words
from validator.normaliser import normalise
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS, LEDGER = HERE / "results", HERE / "spend.jsonl"
AGREE = 0.999


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER)


def calibrate(lines: list[dict], vectors: np.ndarray) -> tuple[float, dict]:
    """The lowest similarity at which same-word pairs at or above it agree ≥ 99.9%, with no restricted-flag disagreement."""
    sims = vectors @ vectors.T
    pairs = []
    for i in range(len(lines)):
        for j in np.nonzero(sims[i, i + 1:] >= 0.5)[0] + i + 1:
            if lines[i]["receipt"] != lines[j]["receipt"] and same_words(lines[i]["text"], lines[j]["text"], OVERLAP):
                a, b = lines[i]["category"], lines[int(j)]["category"]
                pairs.append((float(sims[i, j]), a == b, TAXONOMY[a] == TAXONOMY[b], i, int(j)))
    pairs.sort(reverse=True)
    flag_floor = max((s for s, _, flag, _, _ in pairs if not flag), default=-1.0)
    best, agree = None, 0
    for n, (s, same, *_rest) in enumerate(pairs, start=1):
        agree += same
        if s > flag_floor and agree / n >= AGREE:
            best = (s, n, agree)
    tau = best[0] if best else 1.01
    disagreeing = [(round(s, 4), lines[i]["text"], lines[i]["category"], lines[j]["text"], lines[j]["category"])
                   for s, same, _, i, j in pairs if not same and s >= tau]
    return tau, {"same_word_pairs": len(pairs), "pairs_at_or_above": best[1] if best else 0, "agreeing": best[2] if best else 0,
                 "highest_flag_disagreement": round(flag_floor, 4), "disagreeing_pairs_at_or_above": disagreeing}


def main() -> int:
    gold, variants = load_gold(), load_variants()
    main_receipts = load_receipts()
    dev_receipts, expected = load_dev()
    reworded = reword(main_receipts, variants)
    split = lambda d: (load_receipts(ROOT / "cord" / d / "receipts.jsonl"), cord.load_labels(ROOT / "cord" / d / "labels.jsonl"))
    seen = [(load_receipts(cord.RECEIPTS_PATH), cord.load_labels()), split("test"), split("train"), split("train2")]
    warm, warm_labels = split("train2")
    fresh, labels = split("train3")

    base = Lookup()
    emb = Embedder(base.config["model"], HERE / "cache" / "vectors.npz")
    calib = [{"receipt": r.receipt_id, "text": line.description_raw, "category": lab[line.line_id]["category"]}
             for rs, lab in seen for r in rs for line in r.line_items if lab[line.line_id]["sure"]]
    tau_same, calib_info = calibrate(calib, np.stack(emb.embed([normalise(c["text"]) for c in calib])))
    v32 = {"memory_rule": "same_item", "tau_same": round(tau_same, 6), "word_overlap": OVERLAP}
    print(f"tau_same {tau_same:.4f}: {calib_info['pairs_at_or_above']} of {calib_info['same_word_pairs']} same-word pairs, "
          f"{calib_info['agreeing']} agreeing; disagreeing above: {calib_info['disagreeing_pairs_at_or_above']}")

    llm = client()
    warm_glosses, glosses = translate_all(warm, llm), translate_all(fresh, llm)
    m_warm = {r.receipt_id: classify(r, warm_glosses, llm) for r in warm}
    m = {r.receipt_id: classify(r, glosses, llm) for r in fresh}
    m_score = cord_score([m[r.receipt_id].receipt for r in fresh], fresh, labels)

    arms, rows = {}, []
    for name, overrides in (("V3", {}), ("V3.2", v32)):
        lk = lookup(overrides, emb)
        warmed = {}
        for r in warm:  # review R400–R599 first: not scored
            for line in r.line_items:
                mi, lab = m_warm[r.receipt_id].receipt.items[line.line_id], warm_labels[line.line_id]
                out = lk.learn(line.description_raw, lab["category"], lab["sure"], mi.category, mi.confidence)
                warmed[out] = warmed.get(out, 0) + 1
        classified, arm_rows, learned = stream(name, lk, fresh, labels, glosses, llm, m)
        stats = lookup_stats(arm_rows)
        stats["receipts_no_model_call"] = sum(all(r["decided_by"].startswith("lookup") for r in arm_rows if r["receipt"] == rec.receipt_id)
                                              for rec in fresh if rec.line_items)
        arms[name] = {"score": cord_score(classified, fresh, labels), "lookup": stats, "warm_up": warmed, "learning": learned,
                      "memory_after": len(lk.memory), "risk_after": len(lk.risk)}
        rows += arm_rows

    own_lk = lookup(v32, emb)
    timing = sorted(rid for rid in gold if not in_scope(rid))
    g = translate_all(main_receipts + dev_receipts + reworded, llm)
    own = {}
    for arm, lk in (("M", None), ("V3.2", own_lk)):
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

    V, M_own, V_own = arms["V3.2"], own["M"], own["V3.2"]
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
               "own_sets": {**own, "V3.2_lines_decided_on_32": own_decided}, "repeatability": {"largest_difference": diff},
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
        print(f"{name}: {a['score']}\n   lookup {json.dumps({k: v for k, v in a['lookup'].items() if k != 'wrong'})}\n   wrong {a['lookup']['wrong']}"
              f"\n   warm-up {a['warm_up']} learning {a['learning']} memory {a['memory_after']} risk {a['risk_after']}")
    print("own sets:", {k: (v["receipts"]["exact"], v["receipts"]["items"], v["dev"], v["reworded"]["items"], v["reworded"]["exact"]) for k, v in own.items()},
          "V3.2 decided on the 32:", own_decided)
    print(f"repeatability {diff}; verdict: {metrics['verdict']}; spend ${metrics['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
