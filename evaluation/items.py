"""M9 gate on the real receipts: the v1 classifications against the answer key, item by item.

Reads outputs/v1/classifications.jsonl and never reclassifies. Checks that the output is well formed
(79 valid items, restricted from the taxonomy, no item kind, recipients as annotated by hand), then
scores categories on the 65 in-scope items and lists every miss. Writes outputs/v1/items.json.

`--variants` also classifies all 32 receipts twice more: a fresh repeat of the same requests (how
many answers flip) and a run without the merchant and expense type (the merchant ablation). Their
stats are reported whenever their files exist. They are analysis only: nothing in the pipeline reads them.
"""

import argparse
import json
import sys
from pathlib import Path

from evaluation.gold import in_scope, load_gold
from evaluation.metrics import binary_f1, macro_f1, per_category, recipient_matches
from evaluation.oracle import load_attributes
from validator.classifier import TAXONOMY
from validator.data import ROOT, load_receipts, read_jsonl
from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.models import ClassifiedItem
from validator.pipeline import OUTPUT_DIR, classification_rows, classify_all, write_jsonl

ITEMS_PATH = OUTPUT_DIR / "items.json"
ITEM_FIELDS = set(ClassifiedItem.model_fields) - {"line_id"}


def load_classifications(path: Path = OUTPUT_DIR / "classifications.jsonl") -> dict[str, dict]:
    return {row["line_id"]: row for row in read_jsonl(path)}


def structural_problems(rows: dict[str, dict]) -> list[str]:
    problems = [] if len(rows) == 79 else [f"{len(rows)} items, not 79"]
    for line_id, row in rows.items():
        try:
            item = ClassifiedItem(line_id=line_id, **{k: v for k, v in row.items() if k in ITEM_FIELDS})
        except ValueError as e:
            problems.append(f"{line_id}: not a valid item ({e})")
            continue
        if item.category not in TAXONOMY:
            problems.append(f"{line_id}: unknown category {item.category!r}")
        elif item.restricted != TAXONOMY[item.category]:
            problems.append(f"{line_id}: restricted {item.restricted} but {item.category} is {TAXONOMY[item.category]}")
        if item.item_kind is not None:
            problems.append(f"{line_id}: item kind {item.item_kind}, but no receipt line is one")
    return problems


def recipient_problems(rows: dict[str, dict], attributes: dict[str, dict]) -> list[str]:
    """Every line must name the recipient review/item_attributes.csv gives it, and lines not in it none."""
    problems = []
    for line_id, row in rows.items():
        want = attributes.get(line_id, {"recipient_name": None, "recipient_type": None})
        if not recipient_matches(row["recipient_name"], row["recipient_type"], want["recipient_name"], want["recipient_type"]):
            problems.append(f"{line_id}: got {row['recipient_name']!r} ({row['recipient_type']}), "
                            f"want {want['recipient_name']!r} ({want['recipient_type']})")
    return problems


def score(rows: dict[str, dict]) -> dict:
    gold = load_gold()
    scored = [(line_id, item, rows[line_id]) for receipt_id, g in gold.items() if in_scope(receipt_id)
              for line_id, item in g.items.items()]
    y_true, y_pred = [item.category for _, item, _ in scored], [row["category"] for _, _, row in scored]
    misses = [
        {"line_id": line_id, "text": row["description_raw"], "english": row["english"], "want": item.category,
         "got": row["category"], "confidence": row["confidence"], "restricted_error": item.restricted != row["restricted"],
         "want_restricted": item.restricted, "got_restricted": row["restricted"], "reason": row["reason"]}
        for line_id, item, row in scored if item.category != row["category"]
    ]
    return {
        "items": len(scored),
        "accuracy": sum(t == p for t, p in zip(y_true, y_pred)) / len(scored),
        "macro_f1": macro_f1(y_true, y_pred),
        "categories": len(set(y_true)),
        "restricted_f1": binary_f1([item.restricted for _, item, _ in scored], [row["restricted"] for _, _, row in scored]),
        "per_category": [vars(s) for s in per_category(y_true, y_pred)],
        "misclassified": misses,
        "below_threshold": sorted(line_id for line_id, _, row in scored if row["confidence"] < CONFIDENCE_THRESHOLD),
        "threshold": CONFIDENCE_THRESHOLD,
    }


VARIANTS = {"repeat": {"run": "repeat"}, "no_context": {"context": False}}


def run_variants(offline: bool) -> None:
    """A fresh repeat and a no-context run of all 32 receipts, written beside the v1 output."""
    from validator.llm import LLM
    llm = LLM.offline() if offline else LLM()
    receipts = load_receipts()
    for name, options in VARIANTS.items():
        write_jsonl(classification_rows(*classify_all(receipts, llm, **options)), OUTPUT_DIR / f"classifications_{name}.jsonl")


def variant_stats(rows: dict[str, dict]) -> dict:
    """For each variant run on disk: the answers that differ from v1, and how many in-scope items it gets right."""
    gold = load_gold()
    want = {line_id: item.category for receipt_id, g in gold.items() if in_scope(receipt_id) for line_id, item in g.items.items()}
    out = {}
    for name in VARIANTS:
        path = OUTPUT_DIR / f"classifications_{name}.jsonl"
        if path.exists():
            got = {line_id: row["category"] for line_id, row in load_classifications(path).items()}
            out[name] = {
                "flipped": sorted(line_id for line_id in rows if got[line_id] != rows[line_id]["category"]),
                "correct": sum(got[line_id] == category for line_id, category in want.items()),
            }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", action="store_true", help="also run the repeat and no-context classifications")
    parser.add_argument("--offline", action="store_true", help="with --variants: replay the committed cache only")
    args = parser.parse_args(argv)

    if args.variants:
        run_variants(args.offline)
    rows = load_classifications()
    problems = structural_problems(rows) + recipient_problems(rows, load_attributes())
    report = {"problems": problems, **score(rows), "variants": variant_stats(rows)}

    n = report["items"]
    print(f"{len(rows)} items classified; {len(problems)} structural or recipient problems")
    for problem in problems:
        print(f"  {problem}")
    print(f"\nin scope: {n} items over {report['categories']} answer categories")
    print(f"  accuracy {report['accuracy']:.3f}   macro F1 {report['macro_f1']:.3f}   restricted F1 {report['restricted_f1']:.3f}")
    print(f"  below the EXC-03 threshold ({report['threshold']}): {report['below_threshold'] or 'none'}")
    print("\nper category (support / predicted / correct, F1):")
    for s in report["per_category"]:
        print(f"  {s['category']:22} {s['support']:2} / {s['predicted']:2} / {s['correct']:2}   {s['f1']:.2f}")
    print(f"\nmisclassified: {len(report['misclassified'])}")
    for m in report["misclassified"]:
        flag = "  RESTRICTED-FLAG ERROR" if m["restricted_error"] else ""
        print(f"  {m['line_id']} {m['text']!r}: got {m['got']}, want {m['want']} (confidence {m['confidence']:.3f}){flag}")
        print(f"      reason: {m['reason']}")
    for name, v in report["variants"].items():
        print(f"\n{name}: {v['correct']}/{n} correct; {len(v['flipped'])}/{len(rows)} answers differ from v1 {v['flipped']}")

    ITEMS_PATH.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {ITEMS_PATH.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
