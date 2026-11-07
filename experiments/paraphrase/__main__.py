"""Run the rewording experiment: `uv run python -m experiments.paraphrase` (or `make paraphrase`).

Replaces the text of the 64 itemized lines on the 28 scored receipts with the variants in review.csv
(every row must be marked ok = yes first), classifies them with the unchanged frozen classifier, reruns
the unchanged engine and scores the result against the same answers (PLAN.md). Offline once cache/
holds every call; a miss needs OPENROUTER_API_KEY. Writes results/.
"""

import csv
import json
import sys
from pathlib import Path

from experiments import TEMPERATURE_1
from evaluation.blame import load_classified
from evaluation.evaluate import decision_records, receipt_matches, score_items, score_receipts
from evaluation.gold import in_scope, load_gold
from validator.classifier import classify
from validator.data import ROOT, ReceiptInput, load_receipts, read_jsonl
from validator.llm import LLM, LLMError, ledger_total
from validator.models import ClassifiedReceipt
from validator.pipeline import CONFIDENCE_THRESHOLD, decide_all
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
REVIEW, RESULTS, CACHE, LEDGER = HERE / "review.csv", HERE / "results", HERE / "cache", HERE / "spend.jsonl"
ITEMS_KEPT, RECEIPTS_KEPT = 62, 26  # PLAN.md: at most 3 items and 2 receipts lost; fixed before the run
REPEATS = ("repeat1", "repeat2", "repeat3")  # added after the run (README): fresh calls on each misclassified line's receipt


def load_variants(path: Path = REVIEW, *, require_review: bool = True) -> dict[str, dict]:
    """Line ID → its review row. Refuses rows a person hasn't marked ok = yes."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = {row["line_id"]: row for row in csv.DictReader(f)}
    unreviewed = [line_id for line_id, row in rows.items() if row["ok"].strip().lower() != "yes"]
    if require_review and unreviewed:
        raise SystemExit(f"{len(unreviewed)} of {len(rows)} variants in {path.name} are not marked ok = yes "
                         f"(first: {', '.join(unreviewed[:3])}). Check each one by hand before the run (PLAN.md).")
    return rows


def reword(receipts: list[ReceiptInput], variants: dict[str, dict]) -> list[ReceiptInput]:
    """The scored receipts with each line's text replaced by its variant; nothing else changes."""
    return [r.model_copy(update={"line_items": tuple(
        line.model_copy(update={"description_raw": variants[line.line_id]["variant"]}) if line.line_id in variants else line
        for line in r.line_items)}) for r in receipts if in_scope(r.receipt_id)]


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying this experiment's cache instead.")
        return LLM.offline(cache_dir=CACHE, ledger_path=LEDGER)


def records(classified: list[ClassifiedReceipt]) -> dict[str, dict]:
    """Result records for scoring: the engine's decisions plus each line's category."""
    _, decisions = decide_all(classified)
    out = decision_records(decisions)
    for c in classified:
        out[c.receipt.receipt_id]["line_items"] = [
            {"line_id": line.line_id, "description_raw": line.description_raw, "category": c.items[line.line_id].category,
             "restricted": c.items[line.line_id].restricted, "confidence": c.items[line.line_id].confidence}
            for line in c.receipt.line_items]
    return out


def main() -> int:
    variants = load_variants()
    gold, llm = load_gold(), client()
    reworded = reword(load_receipts(), variants)
    glosses = translate_all(reworded, llm)
    ours = [classify(r, glosses, llm, temperature=1.0).receipt for r in reworded]  # as run, before the switch to 0
    unscored = [c for c in load_classified(TEMPERATURE_1 / "classifications.jsonl") if not in_scope(c.receipt.receipt_id)]  # same cross-receipt context as before
    got = records(ours + unscored)
    original = records(load_classified(TEMPERATURE_1 / "classifications.jsonl"))
    before = {row["line_id"]: row for row in read_jsonl(TEMPERATURE_1 / "classifications.jsonl")}

    items, receipts = score_items(got, gold), score_receipts(got, gold)
    lines = [{
        "line_id": line["line_id"], "kind": variants.get(line["line_id"], {}).get("kind", "unchanged"),
        "original": before[line["line_id"]]["description_raw"],
        "variant": line["description_raw"], "english": glosses.get(line["line_id"], ""),
        "want": gold[rid].items[line["line_id"]].category, "got": line["category"],
        "confidence": round(line["confidence"], 6), "confidence_on_original": round(before[line["line_id"]]["confidence"], 6),
        "right": line["category"] == gold[rid].items[line["line_id"]].category,
    } for rid in sorted(got) if in_scope(rid) for line in got[rid]["line_items"]]
    wrong_receipts = [{"receipt_id": rid, "got": got[rid]["disposition"], "want": gold[rid].disposition,
                       "got_rules": sorted(e["rule_id"] for e in got[rid]["exceptions"]),
                       "want_rules": sorted(e.rule_id for e in gold[rid].exceptions),
                       "stage": "translation or classification" if any(not l["right"] for l in lines if l["line_id"].startswith(rid + "-"))
                       else "a rule, or a field read from the text (recipient, item kind)"}
                      for rid in sorted(got) if in_scope(rid) and not receipt_matches(got[rid], gold[rid])]
    kept_items, kept_receipts = items["accuracy"]["k"], receipts["exact_match"]["k"]
    by_id = {r.receipt_id: r for r in reworded}
    repeats = {m["line_id"]: [{"run": run, "category": item.category, "confidence": round(item.confidence, 6), "reason": item.reason}
                              for run in REPEATS
                              for item in [classify(by_id[m["line_id"].rsplit("-", 1)[0]], glosses, llm, run=run, temperature=1.0).receipt.items[m["line_id"]]]]
               for m in items["misclassified"]}
    metrics = {
        "variants": len(variants), "by_kind": {k: sum(v["kind"] == k for v in variants.values()) for k in ("paraphrase", "same trap")},
        "items": {"original": score_items(original, gold)["accuracy"], "reworded": items["accuracy"],
                  "macro_f1": items["macro_f1"], "restricted_f1": items["restricted_f1"], "misclassified": items["misclassified"]},
        "receipts": {"original": score_receipts(original, gold)["exact_match"], "reworded": receipts["exact_match"],
                     "disposition": receipts["disposition"], "wrong": wrong_receipts},
        "repeats_of_misses": repeats,  # after the run, not in PLAN.md
        "below_threshold": [l["line_id"] for l in lines if l["confidence"] < CONFIDENCE_THRESHOLD],
        "lowest_confidence": sorted(lines, key=lambda l: l["confidence"])[:5],
        "verdict": ("no sign of overfitting to the wording" if kept_items >= ITEMS_KEPT and kept_receipts >= RECEIPTS_KEPT
                    else "evidence that part of 65 / 65 came from the exact wording"),
        "bar": {"items_kept": ITEMS_KEPT, "receipts_kept": RECEIPTS_KEPT},
        "spend_usd": ledger_total(LEDGER),
    }
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "lines.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    print(f"items right: {kept_items} / {items['items']} reworded (original {metrics['items']['original']['k']}); "
          f"macro F1 {items['macro_f1']:.3f}, restricted F1 {items['restricted_f1']:.3f}")
    print(f"receipts exactly right: {kept_receipts} / {receipts['receipts']} (original {metrics['receipts']['original']['k']})")
    print(f"false positives {receipts['disposition']['false_positives']['k']} / 9, "
          f"missed violations {receipts['disposition']['missed_violations']['k']} / 16")
    for m in items["misclassified"]:
        print(f"  item  {m['line_id']} {m['text']!r}: got {m['got']}, want {m['want']}")
    for w in wrong_receipts:
        print(f"  receipt {w['receipt_id']}: got {w['got']} {w['got_rules']}, want {w['want']} {w['want_rules']} ({w['stage']})")
    for line_id, runs in repeats.items():
        print(f"  repeats of {line_id}: " + ", ".join(f"{r['category']} {r['confidence']:.2f}" for r in runs))
    print(f"below the {CONFIDENCE_THRESHOLD} threshold: {metrics['below_threshold'] or 'none'}")
    print(f"verdict: {metrics['verdict']}; spend ${metrics['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
