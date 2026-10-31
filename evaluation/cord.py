"""The CORD pilot: v1, exactly as frozen, on 100 real receipts, scored against hand labels.

`python -m evaluation.cord --run` runs the pipeline on cord/receipts.jsonl (from the cache when it
can) and writes outputs/cord/v1/. Without --run it scores the committed outputs, offline, and
writes outputs/cord/v1/metrics.json. The method was fixed before any model call (cord/README.md,
"Method").

The correct decisions are the engine run on the labels, under the same declared context, so they
inherit the engine's rule logic and the synthetic context. The labels are read only here.
"""

import argparse
import csv
import json
import random
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from evaluation.evaluate import SWEEP
from evaluation.metrics import DISPOSITIONS, confusion, macro_f1, per_category, wilson
from validator.data import ROOT, ReceiptInput, load_receipts, read_jsonl
from validator.llm import CACHE_DIR, LLM, LLMError, cost_usd
from validator.models import ClassifiedItem, ClassifiedReceipt, Decision
from validator.pipeline import VERSION, decide_all, run, write_run

CORD_DIR = ROOT / "cord"
RECEIPTS_PATH = CORD_DIR / "receipts.jsonl"
LABELS_PATH = CORD_DIR / "labels.jsonl"
OUT_DIR = ROOT / "outputs" / "cord" / VERSION
REVIEW_PATH = ROOT / "review" / "cord_labels.csv"
WEIGHTS = (5, 10, 20)  # a wrong restricted flag left unreviewed costs this many reviews; 10 is the rule
BANDS = (0.0, 0.5, 0.9, 0.99, 0.999)  # lower edges of the confidence bands


def load_labels(path: Path = LABELS_PATH) -> dict[str, dict]:
    return {row["line_id"]: row for row in read_jsonl(path)}


def labelled(receipts: list[ReceiptInput], labels: dict[str, dict]) -> list[ClassifiedReceipt]:
    """The receipts as the labels classify them, at full confidence (oracle mode)."""
    return [ClassifiedReceipt(receipt=r, items={
        line.line_id: ClassifiedItem(line_id=line.line_id, category=labels[line.line_id]["category"],
                                     restricted=labels[line.line_id]["restricted"], confidence=1.0)
        for line in r.line_items}) for r in receipts]


def load_system(receipts: list[ReceiptInput], path: Path = OUT_DIR / "classifications.jsonl") -> tuple[list[ClassifiedReceipt], dict[str, dict]]:
    """The committed classifications, as the engine's input, and their rows (with glosses)."""
    rows = {row["line_id"]: row for row in read_jsonl(path)}
    fields = set(ClassifiedItem.model_fields)
    classified = [ClassifiedReceipt(receipt=r, items={
        line.line_id: ClassifiedItem(**{k: v for k, v in rows[line.line_id].items() if k in fields})
        for line in r.line_items}) for r in receipts]
    return classified, rows


def _rate(k: int, n: int) -> dict:
    low, high = wilson(k, n) if n else (None, None)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": [low, high]}


def dispositions(decisions: list[Decision]) -> dict[str, Decision]:
    return {d.receipt_id: d for d in decisions}


def score_items(system: dict[str, dict], labels: dict[str, dict]) -> dict:
    def block(ids: list[str]) -> dict:
        y_true = [labels[i]["category"] for i in ids]
        y_pred = [system[i]["category"] for i in ids]
        r_true = [labels[i]["restricted"] for i in ids]
        r_pred = [bool(system[i]["restricted"]) for i in ids]
        tp = sum(t and p for t, p in zip(r_true, r_pred))
        return {
            "items": len(ids),
            "category_correct": _rate(sum(t == p for t, p in zip(y_true, y_pred)), len(ids)),
            "macro_f1": macro_f1(y_true, y_pred),
            "restricted_precision": _rate(tp, sum(r_pred)),
            "restricted_recall": _rate(tp, sum(r_true)),
            "restricted_flag_correct": _rate(sum(t == p for t, p in zip(r_true, r_pred)), len(ids)),
            "per_category": [asdict(s) for s in per_category(y_true, y_pred)],
            "predicted_categories": dict(Counter(y_pred).most_common()),
        }

    ids = list(labels)
    return {"all": block(ids), "sure": block([i for i in ids if labels[i]["sure"]]),
            "unsure": block([i for i in ids if not labels[i]["sure"]])}


def score_receipts(ours: dict[str, Decision], want: dict[str, Decision]) -> dict:
    ids = sorted(want)
    got, gold = [ours[i].disposition for i in ids], [want[i].disposition for i in ids]
    should_pass = [i for i in ids if want[i].disposition == "pass"]
    flagged = [i for i in ids if ours[i].disposition == "flag"]
    return {
        "receipts": len(ids),
        "gold_dispositions": dict(Counter(gold)),
        "our_dispositions": dict(Counter(got)),
        "confusion": {"rows_gold_cols_ours": list(DISPOSITIONS), "matrix": confusion(gold, got)},
        "disposition_correct": _rate(sum(g == o for g, o in zip(gold, got)), len(ids)),
        "false_flags": _rate(sum(ours[i].disposition == "flag" for i in should_pass), len(should_pass)),
        "false_reviews": _rate(sum(ours[i].disposition == "review" for i in should_pass), len(should_pass)),
        "false_alarms": _rate(sum(ours[i].disposition != "pass" for i in should_pass), len(should_pass)),
        "flag_precision": _rate(sum(want[i].disposition == "flag" for i in flagged), len(flagged)),
        "missed_flags": _rate(sum(ours[i].disposition != "flag" for i in ids if want[i].disposition == "flag"), gold.count("flag")),
        "disagreements": [{
            "receipt_id": i, "ours": ours[i].disposition, "want": want[i].disposition,
            "our_rules": sorted(f.rule_id for f in ours[i].exceptions) + sorted(s.rule_id for s in ours[i].review_signals),
            "want_rules": sorted(f.rule_id for f in want[i].exceptions) + sorted(s.rule_id for s in want[i].review_signals),
        } for i in ids if ours[i].disposition != want[i].disposition
            or {f.rule_id for f in ours[i].exceptions} != {f.rule_id for f in want[i].exceptions}],
    }


def confidence_bands(system: dict[str, dict], labels: dict[str, dict]) -> list[dict]:
    edges = [*BANDS, float("inf")]
    rows = []
    for low, high in zip(edges, edges[1:]):
        ids = [i for i in labels if low <= system[i]["confidence"] < high]
        rows.append({"from": low, "to": None if high == float("inf") else high, "items": len(ids),
                     "category_correct": sum(system[i]["category"] == labels[i]["category"] for i in ids),
                     "restricted_flag_correct": sum(bool(system[i]["restricted"]) == labels[i]["restricted"] for i in ids)})
    return rows


def threshold_costs(classified: list[ClassifiedReceipt], labels: dict[str, dict], want: dict[str, Decision]) -> dict:
    """The pre-registered rule, for illustration on the pilot only. It is not applied to v1."""
    rows = []
    for t in SWEEP:
        items = [(c.receipt.receipt_id, item) for c in classified for item in c.items.values()]
        wrong = sum(item.confidence >= t and item.restricted != labels[item.line_id]["restricted"] for _, item in items)
        reviewed = len({rid for rid, item in items if item.confidence < t})
        ours = dispositions(decide_all(classified, t)[1])
        s = score_receipts(ours, want)
        rows.append({"threshold": t, "items_below": sum(item.confidence < t for _, item in items),
                     "receipts_with_item_below": reviewed, "wrong_restricted_at_or_above": wrong,
                     "cost": {str(w): w * wrong + reviewed for w in WEIGHTS},
                     "flags": s["flag_precision"]["n"], "false_flags": s["false_flags"]["k"],
                     "false_reviews": s["false_reviews"]["k"], "missed_flags": s["missed_flags"]["k"]})
    chosen = {str(w): min(rows, key=lambda r: (r["cost"][str(w)], r["threshold"]))["threshold"] for w in WEIGHTS}
    return {"sweep": rows, "chosen": chosen}


def disagreements(system: dict[str, dict], labels: dict[str, dict]) -> list[dict]:
    return [{"line_id": i, "description_raw": labels[i]["description_raw"], "english": system[i].get("english"),
             "ours": system[i]["category"], "label": labels[i]["category"], "sure": labels[i]["sure"],
             "confidence": float(system[i]["confidence"]), "reason": system[i].get("reason"), "label_note": labels[i]["note"]}
            for i in labels if system[i]["category"] != labels[i]["category"]]


def usage(system: dict[str, dict], receipts: int) -> dict:
    """Tokens and cost of the calls behind the run, read from the committed cache (peak prices)."""
    keys = {row["cache_key"] for row in system.values() if row["cache_key"]}
    calls = {"classify": [json.loads(p.read_text(encoding="utf-8")) for key in sorted(keys) for p in (CACHE_DIR / "classify").glob(f"{key}.json")],
             "translate": [r for p in sorted((CACHE_DIR / "translate").glob("*.json"))
                           if '"CORD-' in (r := json.loads(p.read_text(encoding="utf-8")))["request"]["params"]["messages"][1]["content"]]}
    out = {}
    for purpose, rows in calls.items():
        u = [r["response"]["usage"] for r in rows]
        out[purpose] = {"calls": len(rows), "usd": sum(cost_usd(r["request"]["provider"], r["request"]["params"]["model"], x)
                                                       for r, x in zip(rows, u))}
    out["usd_per_receipt"] = sum(out[p]["usd"] for p in calls) / receipts
    return out


def unsure_as_v1(labels: dict[str, dict], system: dict[str, dict]) -> dict[str, dict]:
    """After the fact, not pre-registered: every unsure label replaced by v1's answer. It bounds how
    much of the receipt score rests on the labeller's guesses."""
    return {i: row if row["sure"] else {**row, "category": system[i]["category"], "restricted": bool(system[i]["restricted"])}
            for i, row in labels.items()}


def build() -> dict:
    receipts, labels = load_receipts(RECEIPTS_PATH), load_labels()
    classified, system = load_system(receipts)
    want = dispositions(decide_all(labelled(receipts, labels))[1])
    ours = dispositions(decide_all(classified)[1])
    want_if_unsure_as_v1 = dispositions(decide_all(labelled(receipts, unsure_as_v1(labels, system)))[1])
    return {
        "note": "v1 as frozen, on the 100 CORD validation receipts, scored against cord/labels.jsonl. The correct "
                "decisions are the engine on the labels under the same declared context.",
        "items": score_items(system, labels),
        "receipts": score_receipts(ours, want),
        "receipts_if_unsure_labels_follow_v1": score_receipts(ours, want_if_unsure_as_v1),
        "confidence_bands": confidence_bands(system, labels),
        "threshold_rule": threshold_costs(classified, labels, want),
        "item_disagreements": disagreements(system, labels),
        "usage": usage(system, len(receipts)),
    }


def write_label_review(receipts: list[ReceiptInput], labels: dict[str, dict], path: Path = REVIEW_PATH) -> None:
    """The hand check fixed in the method: every restricted and unsure label, and a random 30 of the rest.
    It shows no system output. `ok` marks are kept while the label they approved is unchanged."""
    kept = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            kept = {row["line_id"]: row for row in csv.DictReader(f)}
    must = [i for i, row in labels.items() if row["restricted"] or not row["sure"]]
    rest = random.Random(0).sample(sorted(set(labels) - set(must)), 30)
    lines = {line.line_id: (r, line) for r in receipts for line in r.line_items}
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["line_id", "description_raw", "other_lines", "category", "sure", "note", "ok"])
        writer.writeheader()
        for i in sorted(must) + sorted(rest):
            receipt, line = lines[i]
            row = labels[i]
            old = kept.get(i, {})
            writer.writerow({"line_id": i, "description_raw": line.description_raw,
                             "other_lines": " | ".join(x.description_raw for x in receipt.line_items if x.line_id != i),
                             "category": row["category"], "sure": "yes" if row["sure"] else "no", "note": row["note"] or "",
                             "ok": old.get("ok", "") if old.get("category") == row["category"] else ""})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="run the pipeline first (makes calls on a cache miss)")
    args = parser.parse_args(argv)
    if args.run:
        try:
            llm = LLM()
        except LLMError as e:
            print(f"{e}. Replaying the committed cache instead.")
            llm = LLM.offline()
        write_run(run(llm, load_receipts(RECEIPTS_PATH)), OUT_DIR)
    write_label_review(load_receipts(RECEIPTS_PATH), load_labels())
    metrics = build()
    (OUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(metrics)
    return 0


def print_summary(m: dict) -> None:
    for name in ("all", "sure"):
        s = m["items"][name]
        print(f"{name:4} items {s['category_correct']['k']}/{s['items']} right, macro F1 {s['macro_f1']:.3f}, "
              f"restricted precision {s['restricted_precision']['k']}/{s['restricted_precision']['n']}, "
              f"recall {s['restricted_recall']['k']}/{s['restricted_recall']['n']}")
    r = m["receipts"]
    print(f"receipts: ours {r['our_dispositions']}, labels {r['gold_dispositions']}")
    for key in ("false_flags", "false_reviews", "false_alarms", "flag_precision", "missed_flags"):
        x = r[key]
        ci = f" (CI {x['ci95'][0]:.1%}–{x['ci95'][1]:.1%})" if x["n"] else ""
        print(f"  {key}: {x['k']}/{x['n']}{ci}")
    alt = m["receipts_if_unsure_labels_follow_v1"]
    print(f"  if unsure labels followed v1 (after the fact): false flags {alt['false_flags']['k']}/{alt['false_flags']['n']}, "
          f"flag precision {alt['flag_precision']['k']}/{alt['flag_precision']['n']}")
    print(f"threshold rule would pick (illustration only): {m['threshold_rule']['chosen']}")
    print(f"cost: {m['usage']}")


if __name__ == "__main__":
    sys.exit(main())
