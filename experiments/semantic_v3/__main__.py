"""Run experiment 8: `make semantic-v3` (uv run --group semantic python -m experiments.semantic_v3).

Calibrates on CORD validation and test, checks the curated-only system on the project's own sets, then streams
400 fresh CORD training receipts in batches of 50, learning after each, against main's classifier (PLAN.md).
Writes results/metrics.json, results/stream.csv and results/lines.csv.
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
from experiments.semantic_first.semantic import TAXONOMY, decontaminate
from experiments.semantic_v2.__main__ import cord_score
from experiments.semantic_v2.embed import Embedder
from experiments.semantic_v2.lookup import Lookup, load_library
from experiments.semantic_v3.system import Classifier, Decision, Params
from validator.classifier import classify
from validator.data import ROOT, load_receipts
from validator.llm import CACHE_DIR, LLM, LLMError, ledger_total
from validator.normaliser import normalise
from validator.pipeline import decide_all
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
LEDGER = HERE / "spend.jsonl"
BATCH, BLOCK = 50, 100
AGREE, SAFE = 0.995, 0.995
TAU_FAM_GRID = [round(0.70 + 0.01 * i, 2) for i in range(26)]
DELTA_GRID = [round(0.01 * i, 2) for i in range(21)]


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER)


def tau_mem(lines: list[tuple[str, str, str]], vectors: np.ndarray) -> tuple[float, dict]:
    """Lowest similarity at which sure-labelled pairs on different receipts agree ≥ 99.5%, with no restricted-flag disagreement."""
    sims = vectors @ vectors.T
    pairs = [(float(sims[i, j]), lines[i][1] == lines[j][1], TAXONOMY[lines[i][1]] == TAXONOMY[lines[j][1]])
             for i in range(len(lines)) for j in range(i + 1, len(lines)) if lines[i][0] != lines[j][0]]
    pairs.sort(reverse=True)
    flag_floor = max((s for s, _, flag in pairs if not flag), default=-1.0)
    best, agree, n = None, 0, 0
    for s, same, _ in pairs:
        agree, n = agree + same, n + 1
        if s > flag_floor and agree / n >= AGREE:
            best = (s, n, agree)
    tau = best[0] if best else 1.01
    return tau, {"pairs": len(pairs), "pairs_at_or_above": best[1] if best else 0, "agreeing": best[2] if best else 0,
                 "highest_flag_disagreement": round(flag_floor, 4)}


def calibrate_curated(classifier: Classifier, lines: list[dict], vectors: np.ndarray) -> tuple[float, float, dict]:
    """Grid over τ_fam and δ for step 1b on the calibration lines (memory empty)."""
    best, table = None, []
    for tau_fam in TAU_FAM_GRID:
        for delta in DELTA_GRID:
            classifier.params = Params(1.01, tau_fam, delta, classifier.params.safe_cutoff, 0, 0)
            decided = [(line, d) for line, v in zip(lines, vectors) if (d := classifier.decide(line["text"], v, "5812")).decided]
            sure = [(line, d) for line, d in decided if line["sure"]]
            right = sum(d.category == line["category"] for line, d in sure)
            unsafe = sum(TAXONOMY[line["category"]] for line, _ in decided)
            ok = unsafe == 0 and (not sure or right / len(sure) >= SAFE)
            table.append({"tau_fam": tau_fam, "delta": delta, "decided": len(decided), "sure": len(sure), "right": right, "restricted_as_safe": unsafe, "ok": ok})
            key = (len(decided), tau_fam, delta)
            if ok and (best is None or key > best[0]):
                best = (key, tau_fam, delta, (right + 1) / (len(sure) + 2))
    if best is None:
        return 1.01, 1.0, {"note": "no grid point qualified: step 1b decides nothing", "confidence": None}
    chosen = next(r for r in table if r["tau_fam"] == best[1] and r["delta"] == best[2])
    return best[1], best[2], {**chosen, "confidence": round(best[3], 6)}


def repeatability(texts: list[str], embedder: Embedder) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        texts_path, out = Path(tmp) / "texts.json", Path(tmp) / "again.npy"
        texts_path.write_text(json.dumps(texts), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "experiments.semantic_v2.embed", "--recompute", str(out), str(texts_path)],
                       check=True, cwd=ROOT, capture_output=True)
        diff = float(np.abs(np.load(out) - embedder.embed(texts)).max())
    return {"texts": len(texts), "largest_difference": diff, "identical_within_tolerance": diff <= 1e-5}


def main() -> int:
    gold, variants = load_gold(), load_variants()
    main_receipts = load_receipts()
    dev_receipts, expected = load_dev()
    reworded = reword(main_receipts, variants)
    val, val_labels = load_receipts(cord.RECEIPTS_PATH), cord.load_labels()
    test, test_labels = load_receipts(ROOT / "cord" / "test" / "receipts.jsonl"), cord.load_labels(ROOT / "cord" / "test" / "labels.jsonl")
    train, train_labels = load_receipts(ROOT / "cord" / "train" / "receipts.jsonl"), cord.load_labels(ROOT / "cord" / "train" / "labels.jsonl")
    embedder = Embedder(HERE / "cache" / "vectors.npz")
    vec = lambda texts: embedder.embed([normalise(t) for t in texts])

    # The curated library, decontaminated against every test line (the stream's included).
    every_test_line = [line.description_raw for rs in (main_receipts, dev_receipts, reworded, val, test, train) for r in rs for line in r.line_items]
    curated, dropped = decontaminate(load_library(), every_test_line)
    curated_vectors = vec([a.text for a in curated])
    safe_cutoff = Lookup(curated, curated_vectors).cutoff["safe"]

    # Calibration on CORD validation and test only.
    calib = [{"receipt": r.receipt_id, "text": line.description_raw, **{k: labels[line.line_id][k] for k in ("category", "sure")}}
             for rs, labels in ((val, val_labels), (test, test_labels)) for r in rs if r.itemized for line in r.line_items]
    calib_vectors = vec([c["text"] for c in calib])
    sure = [i for i, c in enumerate(calib) if c["sure"]]
    t_mem, mem_info = tau_mem([(calib[i]["receipt"], calib[i]["category"], calib[i]["text"]) for i in sure], calib_vectors[sure])
    probe = Classifier(curated, curated_vectors, Params(1.01, 1.0, 1.0, safe_cutoff, 0, 0))
    t_fam, delta, curated_info = calibrate_curated(probe, calib, calib_vectors)
    params = Params(t_mem, t_fam, delta, safe_cutoff, round(AGREE, 6), curated_info.get("confidence") or 0.0)
    print(f"calibrated: tau_mem {t_mem:.4f} ({mem_info}); tau_fam {t_fam}, delta {delta} ({curated_info}); safe cutoff {safe_cutoff:.4f}")

    llm = client()

    def model(receipts):
        glosses = translate_all(receipts, llm)
        return {r.receipt_id: classify(r, glosses, llm) for r in receipts}

    # 1. Curated-only (no memory) on the project's own sets, against M.
    own = {"receipts": main_receipts, "dev": dev_receipts, "reworded": reworded}
    curated_only = Classifier(curated, curated_vectors, params)
    project, project_lines = {}, []
    for name, rs in own.items():
        m = model(rs)
        decisions = {line.line_id: curated_only.decide(line.description_raw, v, r.mcc)
                     for r in rs if r.itemized for line, v in zip(r.line_items, vec([l.description_raw for l in r.line_items]))}
        project[name] = {"M": [m[r.receipt_id].receipt for r in rs], "V3": [combine(m[r.receipt_id], decisions) for r in rs], "decisions": decisions}
        project_lines += [{"set": name, "line_id": line.line_id, "text": line.description_raw, "step": decisions[line.line_id].step if line.line_id in decisions else "not itemized",
                           "why": decisions[line.line_id].why if line.line_id in decisions else "", "V3": next(c.items[line.line_id].category for c in project[name]["V3"] if line.line_id in c.items),
                           "M": m[r.receipt_id].receipt.items[line.line_id].category} for r in rs for line in r.line_items]
    timing = sorted(rid for rid in gold if not in_scope(rid))
    own_scores = {}
    for arm in ("M", "V3"):
        got = records(project["receipts"][arm])
        rows = score_dev([SimpleNamespace(receipt=c) for c in project["dev"][arm]], expected)
        timing_context = [c for c in project["receipts"][arm] if c.receipt.receipt_id in timing]
        own_scores[arm] = {"receipts": receipts_score(got, gold), "timing": {rid: receipt_matches(got[rid], gold[rid]) for rid in timing},
                           "dev": {"category": sum(r.category_ok for r in rows), "item_kind": sum(r.kind_ok for r in rows), "recipient": sum(r.recipient_ok for r in rows)},
                           "reworded": receipts_score(records(project["reworded"][arm] + timing_context), gold)}
    own_decided = {name: f"{sum(d.decided for d in p['decisions'].values())} of {len(p['decisions'])}" for name, p in project.items()}

    # 2. The stream: 400 fresh receipts, 8 batches of 50, learning after each.
    m_train = model(train)
    system = Classifier(curated, curated_vectors, params)
    stream_decisions: dict[str, Decision] = {}
    stream_rows, learned = [], {}
    for start in range(0, len(train), BATCH):
        batch = train[start:start + BATCH]
        for r in batch:
            if not r.itemized or not r.line_items:
                continue
            vectors = vec([line.description_raw for line in r.line_items])
            for line, v in zip(r.line_items, vectors):
                d = system.decide(line.description_raw, v, r.mcc)
                stream_decisions[line.line_id] = d
                mi = m_train[r.receipt_id].receipt.items[line.line_id]
                label = train_labels[line.line_id]
                stream_rows.append({"batch": start // BATCH + 1, "receipt": r.receipt_id, "line_id": line.line_id, "text": line.description_raw,
                                    "label": label["category"], "label_sure": label["sure"], "step": d.step, "decided": d.decided, "why": d.why,
                                    "lookup": d.category if d.decided else "", "M": mi.category, "M_confidence": round(mi.confidence, 4),
                                    "final": d.category if d.decided else mi.category, "memory_size": len(system.memory), "risk_size": len(system.risk)})
        for r in batch:  # review, then learn
            if not r.line_items:
                continue
            for line, v in zip(r.line_items, vec([line.description_raw for line in r.line_items])):
                label, mi = train_labels[line.line_id], m_train[r.receipt_id].receipt.items[line.line_id]
                outcome = system.learn(normalise(line.description_raw), v, label["category"], label["sure"], mi.category, mi.confidence)
                learned[outcome] = learned.get(outcome, 0) + 1
    final = [combine(m_train[r.receipt_id], stream_decisions) for r in train]
    stream_v3 = cord_score(final, train, train_labels)
    stream_m = cord_score([m_train[r.receipt_id].receipt for r in train], train, train_labels)

    decided = [row for row in stream_rows if row["decided"]]
    decided_sure = [row for row in decided if row["label_sure"]]
    restricted_as_safe = [row for row in decided if TAXONOMY[row["label"]]]
    model_disagrees = [row for row in decided if row["lookup"] != row["M"]]
    blocks = []
    for b in range(0, len(train), BLOCK):
        ids = {r.receipt_id for r in train[b:b + BLOCK]}
        rows = [row for row in stream_rows if row["receipt"] in ids]
        receipts = [r for r in train[b:b + BLOCK] if r.itemized and r.line_items]
        no_call = sum(all(stream_decisions[line.line_id].decided for line in r.line_items) for r in receipts)
        blocks.append({"block": f"{b + 1}–{b + BLOCK}", "lines": len(rows), "decided": sum(r["decided"] for r in rows),
                       "share": round(sum(r["decided"] for r in rows) / max(1, len(rows)), 4),
                       "by_memory": sum(r["step"] == "memory" and r["decided"] for r in rows), "by_curated": sum(r["step"] == "curated" and r["decided"] for r in rows),
                       "receipts_no_model_call": no_call, "receipts": len(receipts)})
    repeat = repeatability(list(dict.fromkeys([normalise(a.text) for a in curated] + [normalise(c["text"]) for c in calib] + [normalise(t) for t in every_test_line])), embedder)

    M, V = own_scores["M"], own_scores["V3"]
    shares = [b["share"] for b in blocks]
    bar = {
        "1 own sets: 28/28, 65/65, 0 FP, 0 missed; timing 4/4; dev ≥42; reworded ≥ M":
            V["receipts"]["exact"] == 28 and V["receipts"]["items"] == 65 and V["receipts"]["false_positives"] == 0 and V["receipts"]["missed"] == 0
            and all(V["timing"].values()) and min(V["dev"].values()) >= 42
            and V["reworded"]["items"] >= M["reworded"]["items"] and V["reworded"]["exact"] >= M["reworded"]["exact"],
        "2 safe: no restricted item decided as safe; ≥99.5% right on sure labels":
            not [r for r in restricted_as_safe if r["label_sure"]]
            and (not decided_sure or sum(r["lookup"] == r["label"] for r in decided_sure) / len(decided_sure) >= SAFE),
        "3 as accurate as main on the stream": stream_v3["lines_right"] >= stream_m["lines_right"] - 3
            and stream_v3["false_alarms"] <= stream_m["false_alarms"] and stream_v3["missed"] <= stream_m["missed"],
        "4 grows: ≥40% in the last 100 receipts, never falling": shares[-1] >= 0.40 and all(a <= b for a, b in zip(shares, shares[1:])),
        "5 repeatable": repeat["identical_within_tolerance"],
    }
    metrics = {
        "params": {"tau_mem": t_mem, "tau_fam": t_fam, "delta": delta, "safe_cutoff": safe_cutoff, "tau_mem_calibration": mem_info, "curated_calibration": curated_info},
        "library": {"written": len(load_library()), "kept": len(curated), "dropped": len(dropped)},
        "own_sets": {"lookup_decided": own_decided, **own_scores},
        "stream": {"M": stream_m, "V3": stream_v3, "blocks": blocks, "learning": learned, "memory_final": len(system.memory), "risk_final": len(system.risk),
                   "lookup_decided": len(decided), "lookup_right_sure": sum(r["lookup"] == r["label"] for r in decided_sure), "lookup_decided_sure": len(decided_sure),
                   "lookup_wrong": [{k: r[k] for k in ("line_id", "text", "lookup", "label", "label_sure", "step")} for r in decided if r["lookup"] != r["label"]],
                   "restricted_decided_as_safe": [{k: r[k] for k in ("line_id", "text", "lookup", "label", "label_sure")} for r in restricted_as_safe],
                   "model_disagrees_with_lookup": {"count": len(model_disagrees), "of": len(decided),
                                                   "lines": [{k: r[k] for k in ("line_id", "text", "lookup", "M", "label")} for r in model_disagrees]}},
        "repeatability": repeat,
        "bar": bar,
        "verdict": "passes" if all(bar.values()) else "fails: " + "; ".join(k for k, ok in bar.items() if not ok),
        "spend_usd": ledger_total(LEDGER),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    for path, rows in ((RESULTS / "stream.csv", stream_rows), (RESULTS / "lines.csv", project_lines)):
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({k: metrics[k] for k in ("params",)}, default=str)[:600])
    print("own sets decided:", own_decided, "| M vs V3:", {a: (s["receipts"]["exact"], s["receipts"]["items"], s["dev"], s["reworded"]["items"], s["reworded"]["exact"]) for a, s in own_scores.items()})
    print("stream blocks:", blocks)
    print("stream M:", stream_m, "\nstream V3:", stream_v3)
    print(f"lookup decided {len(decided)}; sure right {metrics['stream']['lookup_right_sure']}/{len(decided_sure)}; restricted as safe {len(restricted_as_safe)}; model disagrees {len(model_disagrees)}")
    print("learning:", learned, "| repeatability:", repeat)
    print(f"verdict: {metrics['verdict']}; spend ${metrics['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
