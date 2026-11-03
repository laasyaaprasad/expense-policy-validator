"""Part 3 evaluation: every number the brief asks for, computed from committed outputs only.

`make eval` scores outputs/results.jsonl on the 28 in-scope receipts, with the data dictionary's
matching rules, and writes outputs/metrics.json. The same scoring runs on the keyword baseline
(outputs/baseline.jsonl) and on oracle mode, which must come out perfect.

The threshold sweep reruns the engine on the committed classifications at other EXC-03 thresholds. It is
after-the-fact analysis on the answer key: the threshold in use was fixed on the dev set (NOTES.md).
"""

import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from evaluation.blame import blame, load_classified, load_gloss_review
from evaluation.gold import GoldReceipt, in_scope, load_gold
from evaluation.metrics import (DISPOSITIONS, binary_f1, confusion, false_positives, macro_f1, missed_violations,
                                per_category, rule_scores, wilson)
from evaluation.oracle import ORACLE_PATH, load_attributes, run_oracle
from validator.data import ROOT
from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.llm import CACHE_DIR
from validator.models import Decision
from validator.pipeline import OUTPUT_DIR, decide_all

METRICS_PATH = OUTPUT_DIR / "metrics.json"
BASELINE_PATH = ROOT / "outputs" / "baseline.jsonl"
SWEEP = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999)


def load_results(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return {record["receipt_id"]: record for record in map(json.loads, f)}


def _scored(gold: dict[str, GoldReceipt]) -> list[str]:
    return sorted(rid for rid in gold if in_scope(rid))


def _rate(k: int, n: int) -> dict:
    low, high = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n, "ci95": [low, high]}


def receipt_matches(record: dict, gold: GoldReceipt) -> bool:
    """Exactly right: the disposition, every exception with exactly its line IDs, and the signals."""
    ours = {e["rule_id"]: sorted(e["line_ids"]) for e in record["exceptions"]}
    theirs = {e.rule_id: sorted(e.line_ids) for e in gold.exceptions}
    return (record["disposition"] == gold.disposition and ours == theirs
            and {s["rule_id"] for s in record["review_signals"]} == {s.rule_id for s in gold.review_signals})


def check_complete(records: dict[str, dict], gold: dict[str, GoldReceipt]) -> None:
    """Every in-scope receipt has a result, with each gold line exactly once and no others."""
    for rid in _scored(gold):
        if rid not in records:
            raise ValueError(f"no result for {rid}")
        ours = [line["line_id"] for line in records[rid]["line_items"]]
        if sorted(ours) != sorted(gold[rid].items):
            raise ValueError(f"{rid}: result lines {sorted(ours)} differ from gold lines {sorted(gold[rid].items)}")


def score_items(records: dict[str, dict], gold: dict[str, GoldReceipt]) -> dict:
    """Per line item, on the in-scope receipts."""
    check_complete(records, gold)
    pairs = [(line, gold[rid].items[line["line_id"]]) for rid in _scored(gold) for line in records[rid]["line_items"]]
    y_true, y_pred = [g.category for _, g in pairs], [line["category"] for line, _ in pairs]
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    return {
        "items": len(pairs),
        "accuracy": _rate(correct, len(pairs)),
        "macro_f1": macro_f1(y_true, y_pred),
        "categories": len(set(y_true)),
        "restricted_f1": binary_f1([g.restricted for _, g in pairs], [line["restricted"] for line, _ in pairs]),
        "per_category": [asdict(s) for s in per_category(y_true, y_pred)],
        "misclassified": [{"line_id": line["line_id"], "text": line["description_raw"], "got": line["category"], "want": g.category}
                          for line, g in pairs if line["category"] != g.category],
    }


def score_receipts(records: dict[str, dict], gold: dict[str, GoldReceipt]) -> dict:
    """Dispositions, exceptions and signals, on the in-scope receipts. A missing receipt is a KeyError."""
    ids = _scored(gold)
    want, got = [gold[rid].disposition for rid in ids], [records[rid]["disposition"] for rid in ids]
    gold_ex = {rid: {e.rule_id: tuple(e.line_ids) for e in gold[rid].exceptions} for rid in ids}
    our_ex = {rid: {e["rule_id"]: tuple(e["line_ids"]) for e in records[rid]["exceptions"]} for rid in ids}
    gold_sig = {rid: {s.rule_id: () for s in gold[rid].review_signals} for rid in ids}
    our_sig = {rid: {s["rule_id"]: () for s in records[rid]["review_signals"]} for rid in ids}
    exact = [rid for rid in ids if receipt_matches(records[rid], gold[rid])]
    rules, signals = rule_scores(gold_ex, our_ex), rule_scores(gold_sig, our_sig)
    matched = sum(s.matched for s in rules)
    return {
        "receipts": len(ids),
        "exact_match": _rate(len(exact), len(ids)),
        "disposition": {
            "labels": list(DISPOSITIONS),
            "matrix": confusion(want, got),  # rows: correct answer; columns: ours
            "accuracy": _rate(sum(w == g for w, g in zip(want, got)), len(ids)),
            "false_positives": _rate(*false_positives(want, got)),
            "missed_violations": _rate(*missed_violations(want, got)),
            "wrong": [{"receipt_id": rid, "got": records[rid]["disposition"], "want": gold[rid].disposition}
                      for rid in ids if records[rid]["disposition"] != gold[rid].disposition],
        },
        "exceptions": {
            "gold": sum(s.gold for s in rules), "raised": sum(s.raised for s in rules), "matched": matched,
            "precision": matched / max(1, sum(s.raised for s in rules)), "recall": matched / max(1, sum(s.gold for s in rules)),
            "evidence_correct": {"k": sum(s.evidence_correct for s in rules), "n": matched},
            "per_rule": [asdict(s) for s in rules],
        },
        "signals": {"per_rule": [asdict(s) for s in signals]},
    }


def decision_records(decisions: list[Decision]) -> dict[str, dict]:
    """Just enough of a result record for score_receipts, straight from engine decisions."""
    return {d.receipt_id: {"disposition": d.disposition,
                           "exceptions": [{"rule_id": f.rule_id, "line_ids": list(f.line_ids)} for f in d.exceptions],
                           "review_signals": [{"rule_id": s.rule_id} for s in d.review_signals]} for d in decisions}


def threshold_sweep(gold: dict[str, GoldReceipt]) -> list[dict]:
    classified = load_classified()
    confidences = [item.confidence for c in classified if in_scope(c.receipt.receipt_id) for item in c.items.values()]
    rows = []
    for t in SWEEP:
        s = score_receipts(decision_records(decide_all(classified, t)[1]), gold)
        rows.append({"threshold": t, "items_below": sum(c < t for c in confidences),
                     "exact_match": s["exact_match"]["k"], "disposition_correct": s["disposition"]["accuracy"]["k"],
                     "false_positives": s["disposition"]["false_positives"]["k"],
                     "missed_violations": s["disposition"]["missed_violations"]["k"]})
    return rows


def out_of_scope(records: dict[str, dict], gold: dict[str, GoldReceipt]) -> list[dict]:
    """The four timing receipts: not scored, but the clearest failures, so they are shown."""
    oos = {rid for rid in gold if not in_scope(rid)}
    stages = {b.receipt_id: b.stage for b in blame(load_classified(), gold, load_attributes(), load_gloss_review(), run_oracle(), oos)}
    return [{"receipt_id": rid, "got": records[rid]["disposition"], "want": gold[rid].disposition,
             "got_rules": sorted(e["rule_id"] for e in records[rid]["exceptions"]) + sorted(s["rule_id"] for s in records[rid]["review_signals"]),
             "want_rules": sorted(e.rule_id for e in gold[rid].exceptions) + sorted(s.rule_id for s in gold[rid].review_signals),
             "stage": stages.get(rid, "none")} for rid in sorted(oos)]


def usage(records: dict[str, dict]) -> dict:
    """Tokens and cost of the calls behind this run, read from the committed cache (peak prices)."""
    from validator.llm import cost_usd
    rows = (OUTPUT_DIR / "classifications.jsonl").read_text(encoding="utf-8").splitlines()
    keys = {row["cache_key"] for row in map(json.loads, rows) if row["cache_key"]}
    calls = {"classify": [json.loads(p.read_text(encoding="utf-8")) for key in sorted(keys) for p in (CACHE_DIR / "classify").glob(f"{key}.json")],
             "translate": [r for p in sorted((CACHE_DIR / "translate").glob("*.json"))
                           if '"RCP-' in (r := json.loads(p.read_text(encoding="utf-8")))["request"]["params"]["messages"][1]["content"]]}
    out = {}
    for purpose, rows in calls.items():
        u = [r["response"]["usage"] for r in rows]
        provider, model = (rows[0]["request"]["provider"], rows[0]["request"]["params"]["model"]) if rows else (None, None)
        out[purpose] = {
            "calls": len(rows),
            "prompt_tokens": sum(x.get("prompt_tokens", 0) for x in u),
            "prompt_cache_hit_tokens": sum(x.get("prompt_cache_hit_tokens", 0) for x in u),
            "completion_tokens": sum(x.get("completion_tokens", 0) for x in u),
            "usd": sum(cost_usd(provider, model, x) for x in u),
        }
    receipts = len(records)
    out["per_receipt"] = {key: sum(out[p][key] for p in calls) / receipts
                          for key in ("prompt_tokens", "prompt_cache_hit_tokens", "completion_tokens", "usd")}
    return out


def build() -> dict:
    gold = load_gold()
    records = load_results(OUTPUT_DIR / "results.jsonl")
    blames = blame(load_classified(), gold, load_attributes(), load_gloss_review(), run_oracle())
    metrics = {
        "note": "The first scored run after the config freeze, on the 28 in-scope receipts. Nothing was changed after seeing a gold error (NOTES.md, 'The final run'); later changes (the timing rules, fixes found in review) changed no scored decision.",
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "items": score_items(records, gold),
        **score_receipts(records, gold),
        "blame": {"mismatched": len(blames), "by_stage": dict(Counter(b.stage for b in blames)),
                  "receipts": [{"receipt_id": b.receipt_id, "stage": b.stage, "detail": b.detail} for b in blames]},
        "out_of_scope": out_of_scope(records, gold),
        "threshold_sweep": {"note": "after-the-fact analysis on the answer key; the threshold was chosen on the dev set",
                            "rows": threshold_sweep(gold)},
        "oracle": score_receipts(load_results(ORACLE_PATH), gold)["exact_match"],
        "usage": usage(records),
    }
    if BASELINE_PATH.exists():
        baseline = load_results(BASELINE_PATH)
        metrics["baseline"] = {"items": score_items(baseline, gold), **score_receipts(baseline, gold)}
    return metrics


def to_json(metrics: dict) -> str:
    return json.dumps(metrics, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    metrics = build()
    METRICS_PATH.write_text(to_json(metrics), encoding="utf-8")
    print_summary(metrics)
    print(f"\nwrote {METRICS_PATH.relative_to(ROOT)}")
    return 0


def print_summary(m: dict) -> None:
    def pct(r):
        return f"{r['k']}/{r['n']} ({r['rate']:.0%}, 95% CI {r['ci95'][0]:.0%}–{r['ci95'][1]:.0%})"

    systems = {"system": m} | ({"baseline": m["baseline"]} if "baseline" in m else {})
    for name, s in systems.items():
        d, e = s["disposition"], s["exceptions"]
        print(f"\n== {name} ==")
        print(f"items: macro F1 {s['items']['macro_f1']:.3f} over {s['items']['categories']} categories, "
              f"restricted F1 {s['items']['restricted_f1']:.3f}, accuracy {pct(s['items']['accuracy'])}")
        print(f"receipts exactly right: {pct(s['exact_match'])}")
        print("disposition (rows correct, columns ours):  " + "  ".join(f"{x:>6}" for x in d["labels"]))
        for label, row in zip(d["labels"], d["matrix"]):
            print(f"  {label:>6}  {sum(row):2} │ " + "  ".join(f"{x:>6}" for x in row))
        print(f"false positives {pct(d['false_positives'])}; missed violations {pct(d['missed_violations'])}")
        print(f"exceptions: {e['matched']} of {e['gold']} found, {e['raised']} raised; evidence exact on {e['evidence_correct']['k']}/{e['evidence_correct']['n']}")
    print(f"\nblame: {m['blame']['mismatched']} mismatched in scope {m['blame']['by_stage'] or ''}")
    for o in m["out_of_scope"]:
        print(f"  out of scope {o['receipt_id']}: {o['got']} {o['got_rules']} vs {o['want']} {o['want_rules']} → {o['stage']}")
    print("threshold sweep (after the fact): " + ", ".join(f"{r['threshold']}: {r['items_below']} below, {r['exact_match']} exact" for r in m["threshold_sweep"]["rows"]))
    print(f"oracle mode: {m['oracle']['k']}/{m['oracle']['n']} exact")
    u = m["usage"]["per_receipt"]
    print(f"usage per receipt: {u['prompt_tokens']:.0f} prompt ({u['prompt_cache_hit_tokens']:.0f} cached), {u['completion_tokens']:.0f} completion, ${u['usd']:.5f}")


if __name__ == "__main__":
    sys.exit(main())
