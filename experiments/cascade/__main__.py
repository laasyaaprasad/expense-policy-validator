"""Run the cascade experiment: `uv run python -m experiments.cascade` (or `make cascade`).

Replays committed LLM answers and cached embeddings, so it is offline and free once
cache/embeddings.npz holds every text; a missing vector needs OPENROUTER_API_KEY. Writes results/.
"""

import csv
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

from evaluation import cord
from evaluation.blame import load_classified
from evaluation.dev import load_dev
from evaluation.evaluate import decision_records, receipt_matches, score_receipts
from evaluation.gold import in_scope, load_gold
from evaluation.metrics import binary_f1, macro_f1, wilson
from experiments.cascade.cascade import (LIST_PRICE, Arm, CallTokens, Routed, choose_threshold, escalation_reason,
                                         fit_call_tokens, llm_cost, route)
from validator.models import ClassifiedReceipt
from experiments.cascade.cheap import Prototype, Tfidf, definition_prototypes, embedding_sims, nearest
from experiments.cascade.embed import HOST, MODEL, Embedder, ledger
from validator.classifier import TAXONOMY
from validator.data import ROOT, ReceiptInput, load_receipts, read_jsonl
from validator.models import ClassifiedItem
from validator.normaliser import normalise
from validator.pipeline import decide_all

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ARMS = (
    Arm("llm_only", "-", escalates=True),
    Arm("embedding_only", "embedding", escalates=False),
    Arm("embedding_cascade", "embedding", escalates=True),
    Arm("embedding_cascade_conservative", "embedding", escalates=True, conservative=True),
    Arm("tfidf_only", "tfidf", escalates=False),
    Arm("tfidf_cascade", "tfidf", escalates=True),
    Arm("tfidf_cascade_conservative", "tfidf", escalates=True, conservative=True),
)


def _rate(k: int, n: int) -> dict:
    low, high = wilson(k, n) if n else (None, None)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": [low, high]}


def _items(classified) -> dict[str, ClassifiedItem]:
    return {line_id: item for c in classified for line_id, item in c.items.items()}


def _llm_only(receipts: list[ReceiptInput], llm: dict[str, ClassifiedItem]) -> Routed:
    classified = [ClassifiedReceipt(receipt=r, items={line.line_id: llm[line.line_id] for line in r.line_items}) for r in receipts]
    source = {line.line_id: "llm" if r.itemized else "fixed" for r in receipts for line in r.line_items}
    return Routed(classified, source, {line_id: "always" for line_id in source})


def _escalation(routed: Routed, receipts: list[ReceiptInput]) -> dict:
    itemized = [line.line_id for r in receipts if r.itemized for line in r.line_items]
    needing_call = [r for r in receipts if any(routed.source[line.line_id] == "llm" for line in r.line_items)]
    reasons: dict[str, int] = {}
    for line_id in itemized:
        if routed.source[line_id] == "llm":
            reasons[routed.reason[line_id]] = reasons.get(routed.reason[line_id], 0) + 1
    return {"lines_escalated": _rate(sum(routed.source[i] == "llm" for i in itemized), len(itemized)),
            "receipts_needing_llm": _rate(len(needing_call), len(receipts)), "reasons": reasons}


def _cost(routed: Routed, receipts: list[ReceiptInput], arm: Arm, classify: CallTokens, translate_usd: float,
          embed_usd_per_line: float) -> dict:
    model_usd = llm_cost(receipts, routed.source, classify, translate_usd)
    embedded = sum(len(r.line_items) for r in receipts if r.itemized) if arm.scorer == "embedding" else 0
    embed_usd = embedded * embed_usd_per_line
    return {"llm_usd": model_usd, "embedding_usd": embed_usd, "per_receipt_usd": (model_usd + embed_usd) / len(receipts)}


def main() -> int:
    RESULTS.mkdir(exist_ok=True)

    # --- the three sets, with the LLM's committed answers ---------------------------------------
    dev_receipts, expected = load_dev()
    dev_rows = {row["line_id"]: row for row in read_jsonl(ROOT / "outputs" / "dev" / "configured.jsonl")}
    dev_llm = {i: ClassifiedItem(line_id=i, category=r["got"], restricted=TAXONOMY[r["got"]], confidence=r["confidence"])
               for i, r in dev_rows.items()}
    dev_want = {i: e["category"] for i, e in expected.items()}

    receipts = load_receipts()
    gold = load_gold()
    receipt_llm = _items(load_classified())
    scored_receipts = [r for r in receipts if in_scope(r.receipt_id)]
    receipt_want = {line_id: item.category for rid, g in gold.items() if in_scope(rid) for line_id, item in g.items.items()}

    cord_receipts = load_receipts(cord.RECEIPTS_PATH)
    cord_labels = cord.load_labels()
    cord_llm = _items(cord.load_system(cord_receipts)[0])
    cord_want_decisions = cord.dispositions(decide_all(cord.labelled(cord_receipts, cord_labels))[1])

    # --- the cheap stage's prototypes and scores ------------------------------------------------
    prototypes = definition_prototypes() + [
        Prototype(normalise(line.description_raw), dev_want[line.line_id], line.line_id)
        for r in dev_receipts for line in r.line_items]
    categories = [p.category for p in prototypes]
    sets = {
        "dev": [line for r in dev_receipts for line in r.line_items],
        "receipts": [line for r in receipts if r.itemized for line in r.line_items],
        "cord": [line for r in cord_receipts if r.itemized for line in r.line_items],
    }
    texts = {name: [normalise(line.description_raw) for line in lines] for name, lines in sets.items()}
    dev_exclude = np.array([[p.source == line.line_id for p in prototypes] for line in sets["dev"]])

    embedder = Embedder()
    prototype_vectors = embedder.embed([p.text for p in prototypes])
    tfidf = Tfidf(prototypes)
    cheap: dict[str, dict[str, object]] = {"embedding": {}, "tfidf": {}}
    for name, lines in sets.items():
        exclude = dev_exclude if name == "dev" else None
        sims = {"embedding": embedding_sims(embedder.embed(texts[name]), prototype_vectors), "tfidf": tfidf.sims(texts[name])}
        for scorer, matrix in sims.items():
            for line, label in zip(lines, nearest(matrix, categories, exclude)):
                cheap[scorer][line.line_id] = label

    # --- thresholds, chosen on dev only (PLAN.md) ----------------------------------------------
    thresholds, threshold_tables = {}, {}
    for arm in ARMS:
        if arm.name == "llm_only" or not arm.escalates:
            continue
        dev = [(text, cheap[arm.scorer][line.line_id], dev_want[line.line_id]) for text, line in zip(texts["dev"], sets["dev"])]
        thresholds[arm.name], threshold_tables[arm.name] = choose_threshold(dev, arm.conservative)

    # --- cost model, from the spend ledger -----------------------------------------------------
    spend = [json.loads(row) for row in (ROOT / "outputs" / "spend.jsonl").read_text(encoding="utf-8").splitlines() if row.strip()]
    lines_per_call: dict[str, int] = {}
    for path in (ROOT / "outputs" / "classifications.jsonl", cord.OUT_DIR / "classifications.jsonl"):
        for row in read_jsonl(path):
            if row.get("cache_key"):
                lines_per_call[row["cache_key"]] = lines_per_call.get(row["cache_key"], 0) + 1
    classify_calls = [(lines_per_call[e["cache_key"]], e["prompt_tokens"], e["completion_tokens"])
                      for e in spend if e["purpose"] == "classify" and e["prompt_version"] == "classify-v1" and e["cache_key"] in lines_per_call]
    classify_tokens = fit_call_tokens(classify_calls)
    translations = [e for e in spend if e["purpose"] == "translate"]
    translate_usd = (LIST_PRICE["in"] * np.mean([e["prompt_tokens"] for e in translations])
                     + LIST_PRICE["out"] * np.mean([e["completion_tokens"] for e in translations]))
    embed_calls = [e for e in ledger() if e["purpose"] == "embed" and e["usd"] is not None]
    embed_usd_per_line = sum(e["usd"] for e in embed_calls) / max(1, sum(e["inputs"] for e in embed_calls))

    # --- every arm on every set -----------------------------------------------------------------
    results: dict[str, dict] = {"dev": {}, "receipts": {}, "cord": {}}
    sources: dict[str, dict[str, str]] = {}
    for arm in ARMS:
        threshold = thresholds.get(arm.name, math.inf)
        pick = (lambda rs, llm: _llm_only(rs, llm)) if arm.name == "llm_only" else (
            lambda rs, llm: route(rs, llm, cheap[arm.scorer], arm, threshold))

        routed = pick(dev_receipts, dev_llm)
        got = _items(routed.classified)
        results["dev"][arm.name] = {
            "category_correct": _rate(sum(got[i].category == w for i, w in dev_want.items()), len(dev_want)),
            "restricted_errors": sum(got[i].restricted != TAXONOMY[w] for i, w in dev_want.items()),
            **_escalation(routed, dev_receipts),
        }
        sources[f"dev/{arm.name}"] = routed.source

        routed = pick(receipts, receipt_llm)
        got = _items(routed.classified)
        records = decision_records(decide_all(routed.classified)[1])
        decisions = score_receipts(records, gold)
        ids = sorted(receipt_want)
        y_true, y_pred = [receipt_want[i] for i in ids], [got[i].category for i in ids]
        results["receipts"][arm.name] = {
            "category_correct": _rate(sum(t == p for t, p in zip(y_true, y_pred)), len(ids)),
            "macro_f1": macro_f1(y_true, y_pred),
            "restricted_f1": binary_f1([TAXONOMY[t] for t in y_true], [got[i].restricted for i in ids]),
            "exact_match": decisions["exact_match"],
            "false_positives": decisions["disposition"]["false_positives"],
            "missed_violations": decisions["disposition"]["missed_violations"],
            "wrong_receipts": sorted(set(ids_scored(records)) - set(ids_exact(records, gold))),
            **_escalation(routed, scored_receipts),
            "cost": _cost(routed, scored_receipts, arm, classify_tokens, translate_usd, embed_usd_per_line),
        }
        sources[f"receipts/{arm.name}"] = routed.source

        routed = pick(cord_receipts, cord_llm)
        got = _items(routed.classified)
        items = cord.score_items({i: {"category": it.category, "restricted": it.restricted} for i, it in got.items()}, cord_labels)["all"]
        decided = cord.score_receipts(cord.dispositions(decide_all(routed.classified)[1]), cord_want_decisions)
        results["cord"][arm.name] = {
            "category_correct": items["category_correct"], "macro_f1": items["macro_f1"],
            "restricted_precision": items["restricted_precision"], "restricted_recall": items["restricted_recall"],
            "false_alarms": decided["false_alarms"], "flag_precision": decided["flag_precision"], "missed_flags": decided["missed_flags"],
            **_escalation(routed, cord_receipts),
            "cost": _cost(routed, cord_receipts, arm, classify_tokens, translate_usd, embed_usd_per_line),
        }
        sources[f"cord/{arm.name}"] = routed.source

    # --- after-the-fact sweeps: escalation against accuracy, plain cascades ----------------------
    sweep_rows = []
    wants = {"dev": dev_want, "receipts": receipt_want, "cord": {i: l["category"] for i, l in cord_labels.items()}}
    for scorer in ("embedding", "tfidf"):
        arm = Arm(f"{scorer}_cascade", scorer, escalates=True)
        for name, lines in sets.items():
            llm = {"dev": dev_llm, "receipts": receipt_llm, "cord": cord_llm}[name]
            ids = [line.line_id for line in lines if line.line_id in wants[name]]
            margins = [cheap[scorer][i].margin for i in ids]
            for t in sorted({0.0, *np.quantile(margins, np.linspace(0.1, 0.9, 9)).tolist(), math.inf}):
                right = flags = escalated = 0
                for line in lines:
                    if line.line_id not in wants[name]:
                        continue
                    label, want = cheap[scorer][line.line_id], wants[name][line.line_id]
                    why = escalation_reason(normalise(line.description_raw), label, t, False)
                    answer = llm[line.line_id].category if why else label.category
                    escalated += why is not None
                    right += answer == want
                    flags += TAXONOMY[answer] != TAXONOMY[want]
                sweep_rows.append({"scorer": scorer, "set": name, "threshold": t, "lines": len(ids),
                                   "escalated": escalated, "category_correct": right, "restricted_errors": flags})

    # --- write -----------------------------------------------------------------------------------
    metrics = {
        "note": "Post-freeze experiment (PLAN.md). Escalated lines reuse the committed LLM answers; costs are at list price.",
        "embedding": {"model": MODEL, "host": HOST, "via": "OpenRouter"},
        "prototypes": {"definitions_and_terms": len(definition_prototypes()), "dev_items": len(sets["dev"])},
        "thresholds": thresholds,
        "threshold_tables": threshold_tables,
        "cost_model": {"classify_call_tokens": asdict(classify_tokens), "classify_calls_fitted": len(classify_calls),
                       "translate_usd_per_call": translate_usd, "embedding_usd_per_line": embed_usd_per_line,
                       "list_price_usd_per_token": LIST_PRICE},
        "sets": results,
    }
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, default=float) + "\n", encoding="utf-8")
    with (RESULTS / "sweep.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(sweep_rows[0]))
        writer.writeheader()
        writer.writerows(sweep_rows)
    with (RESULTS / "lines.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["set", "line_id", "text", "language", "correct", "llm", "embedding", "embedding_margin",
                         "tfidf", "tfidf_margin", *[f"source:{a.name}" for a in ARMS[2:]]])
        llms = {"dev": dev_llm, "receipts": receipt_llm, "cord": cord_llm}
        for name, lines in sets.items():
            for line in lines:
                e, t = cheap["embedding"][line.line_id], cheap["tfidf"][line.line_id]
                # The receipts' answer key is the organisers' and is not redistributed (.gitignore), so it stays out.
                correct = "" if name == "receipts" else wants[name].get(line.line_id, "")
                writer.writerow([name, line.line_id, line.description_raw, line.language, correct,
                                 llms[name][line.line_id].category, e.category, f"{e.margin:.4f}", t.category, f"{t.margin:.4f}",
                                 *[sources[f"{name}/{a.name}"][line.line_id] for a in ARMS[2:]]])
    (RESULTS / "summary.md").write_text(summary(metrics), encoding="utf-8")
    print(summary(metrics))
    return 0


def ids_scored(records: dict[str, dict]) -> list[str]:
    return [rid for rid in records if in_scope(rid)]


def ids_exact(records: dict[str, dict], gold) -> list[str]:
    return [rid for rid in ids_scored(records) if receipt_matches(records[rid], gold[rid])]


def _fmt(rate: dict) -> str:
    return f"{rate['k']} / {rate['n']}"


def summary(m: dict) -> str:
    t = m["thresholds"]
    out = ["# Cascade experiment: results", "",
           "Generated by `make cascade`. Read with PLAN.md; escalated lines reuse the committed LLM answers.", "",
           "**Thresholds chosen on dev:** " + ", ".join(f"{k} {v:.4f}" if v != math.inf else f"{k} ∞ (all escalate)" for k, v in t.items()), ""]
    out += ["## 28 scored receipts (65 lines)", "",
            "| Arm | Lines right | Restricted F1 | Exactly right | False positives | Missed | Lines to LLM | Receipts to LLM | $ / receipt |",
            "|---|---|---|---|---|---|---|---|---|"]
    for arm, r in m["sets"]["receipts"].items():
        out.append(f"| {arm} | {_fmt(r['category_correct'])} | {r['restricted_f1']:.3f} | {_fmt(r['exact_match'])} | "
                   f"{_fmt(r['false_positives'])} | {_fmt(r['missed_violations'])} | {_fmt(r['lines_escalated'])} | "
                   f"{_fmt(r['receipts_needing_llm'])} | {r['cost']['per_receipt_usd']:.6f} |")
    out += ["", "## CORD (100 receipts, 270 lines)", "",
            "| Arm | Lines right | Restricted P / R | False alarms | Flag precision | Missed flags | Lines to LLM | $ / receipt |",
            "|---|---|---|---|---|---|---|---|"]
    for arm, r in m["sets"]["cord"].items():
        out.append(f"| {arm} | {_fmt(r['category_correct'])} | {_fmt(r['restricted_precision'])} · {_fmt(r['restricted_recall'])} | "
                   f"{_fmt(r['false_alarms'])} | {_fmt(r['flag_precision'])} | {_fmt(r['missed_flags'])} | "
                   f"{_fmt(r['lines_escalated'])} | {r['cost']['per_receipt_usd']:.6f} |")
    out += ["", "## Dev set (43 lines; cheap stage scored leave-one-out)", "",
            "| Arm | Lines right | Restricted errors | Lines to LLM |", "|---|---|---|---|"]
    for arm, r in m["sets"]["dev"].items():
        out.append(f"| {arm} | {_fmt(r['category_correct'])} | {r['restricted_errors']} | {_fmt(r['lines_escalated'])} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
