"""Blame report: for each in-scope receipt the pipeline gets wrong, the stage that caused it.

Each check corrects one stage's output and reruns retrieval and the engine on all 32 receipts, so
cross-receipt totals follow. The first correction that makes the receipt match the answer key names
the stage. Checks run in this order:

1. rule logic: oracle mode (gold categories, every rule) also gets the receipt wrong.
2. translation: correcting the receipt's misclassified lines whose gloss was marked wrong fixes it.
3. classification: correcting all of the receipt's misclassified lines fixes it.
4. recipient extraction: setting the receipt's recipients to the hand annotation fixes it.
5. classification (cross-receipt): correcting categories and recipients on every receipt fixes it.
6. retrieval: running every rule instead of the retrieved ones fixes it.
7. confidence threshold: turning EXC-03 off (threshold 0) fixes it.
8. unknown: none of these does. The tests fail until it is explained.

`python -m evaluation.blame` reads outputs/classifications.jsonl and writes outputs/blame.json.
"""

import csv
import json
import sys
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from evaluation.gold import GoldReceipt, in_scope, load_gold
from evaluation.oracle import Comparison, compare, load_attributes, run_oracle
from validator.classifier import TAXONOMY
from validator.data import ROOT, load_receipts, read_jsonl
from validator.engine import evaluate
from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.models import ClassifiedItem, ClassifiedReceipt, Decision
from validator.pipeline import OUTPUT_DIR, decide_all
from validator.translate import REVIEW_PATH

BLAME_PATH = OUTPUT_DIR / "blame.json"
STAGES = ("rule logic", "translation", "classification", "recipient extraction",
          "classification (cross-receipt)", "retrieval", "confidence threshold", "unknown")
NO_RECIPIENT = {"recipient_name": None, "recipient_type": None}


@dataclass(frozen=True)
class Blame:
    receipt_id: str
    stage: str
    detail: str
    comparison: Comparison


def load_classified(path: Path = OUTPUT_DIR / "classifications.jsonl") -> list[ClassifiedReceipt]:
    """The committed classifications, as the engine's input."""
    rows: dict[str, dict] = {}
    for row in read_jsonl(path):
        rows.setdefault(row["receipt_id"], {})[row["line_id"]] = ClassifiedItem(
            line_id=row["line_id"], **{k: row[k] for k in set(ClassifiedItem.model_fields) - {"line_id"}})
    return [ClassifiedReceipt(receipt=r, items=rows[r.receipt_id]) for r in load_receipts()]


def load_gloss_review(path: Path = REVIEW_PATH) -> dict[str, bool]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row["line_id"]: row["ok"].strip().lower() == "yes" for row in csv.DictReader(f)}


def _misclassified(c: ClassifiedReceipt, gold: GoldReceipt) -> list[str]:
    return [line_id for line_id, item in c.items.items() if item.category != gold.items[line_id].category]


def _with(c: ClassifiedReceipt, updates: dict[str, dict]) -> ClassifiedReceipt:
    items = {line_id: item.model_copy(update=updates[line_id]) if line_id in updates else item for line_id, item in c.items.items()}
    return c.model_copy(update={"items": items})


def _gold_categories(c: ClassifiedReceipt, gold: GoldReceipt, line_ids: Iterable[str]) -> ClassifiedReceipt:
    return _with(c, {line_id: {"category": gold.items[line_id].category, "restricted": TAXONOMY[gold.items[line_id].category]}
                     for line_id in line_ids})


def _hand_recipients(c: ClassifiedReceipt, attributes: dict[str, dict]) -> ClassifiedReceipt:
    return _with(c, {line_id: {key: attributes.get(line_id, NO_RECIPIENT)[key] for key in NO_RECIPIENT} for line_id in c.items})


def blame(classified: list[ClassifiedReceipt], gold: dict[str, GoldReceipt], attributes: dict[str, dict],
          gloss_ok: dict[str, bool], oracle: list[Decision], scored: Collection[str] | None = None) -> list[Blame]:
    """One Blame per scored receipt the pipeline gets wrong. `scored` defaults to the in-scope receipts."""
    scored = scored if scored is not None else [c.receipt.receipt_id for c in classified if in_scope(c.receipt.receipt_id)]
    by_id = {c.receipt.receipt_id: c for c in classified}
    oracle_by_id = {d.receipt_id: d for d in oracle}

    def fixed(rid: str, changed: dict[str, ClassifiedReceipt] | None = None, *, all_rules: bool = False,
              threshold: float = CONFIDENCE_THRESHOLD) -> bool:
        """Does the receipt match gold once `changed` replaces some receipts' classifications?"""
        receipts = [(changed or {}).get(c.receipt.receipt_id, c) for c in classified]
        decisions = evaluate(receipts, threshold=threshold) if all_rules else decide_all(receipts, threshold)[1]
        return compare(next(d for d in decisions if d.receipt_id == rid), gold[rid]).matches

    def corrected(c: ClassifiedReceipt) -> ClassifiedReceipt:
        g = gold[c.receipt.receipt_id]
        return _hand_recipients(_gold_categories(c, g, _misclassified(c, g)), attributes)

    _, decisions = decide_all(classified)
    blames = []
    for decision in decisions:
        rid = decision.receipt_id
        comparison = compare(decision, gold[rid])
        if rid not in scored or comparison.matches:
            continue
        c, g = by_id[rid], gold[rid]
        wrong = _misclassified(c, g)
        bad_gloss = [line_id for line_id in wrong if gloss_ok.get(line_id) is False]
        changes = ", ".join(f"{line_id}: {c.items[line_id].category} → {g.items[line_id].category}" for line_id in wrong)

        if not compare(oracle_by_id[rid], g).matches:
            stage, detail = "rule logic", "oracle mode also disagrees"
        elif bad_gloss and fixed(rid, {rid: _gold_categories(c, g, bad_gloss)}):
            stage, detail = "translation", f"gloss marked wrong on {', '.join(bad_gloss)}; {changes}"
        elif wrong and fixed(rid, {rid: _gold_categories(c, g, wrong)}):
            stage, detail = "classification", changes
        elif fixed(rid, {rid: _hand_recipients(c, attributes)}):
            stage, detail = "recipient extraction", "; ".join(
                f"{line_id}: got {item.recipient_name!r} ({item.recipient_type})" for line_id, item in c.items.items()
                if any(getattr(item, key) != attributes.get(line_id, NO_RECIPIENT)[key] for key in NO_RECIPIENT))
        elif fixed(rid, {o.receipt.receipt_id: corrected(o) for o in classified}):
            others = [f"{o.receipt.receipt_id}: {', '.join(_misclassified(o, gold[o.receipt.receipt_id]))}"
                      for o in classified if o.receipt.receipt_id != rid and _misclassified(o, gold[o.receipt.receipt_id])]
            stage, detail = "classification (cross-receipt)", "; ".join(others) or "recipients on other receipts"
        elif fixed(rid, all_rules=True):
            stage, detail = "retrieval", "every rule run instead of the retrieved ones fixes it"
        elif fixed(rid, threshold=0):
            stage, detail = "confidence threshold", "with EXC-03 off the receipt matches"
        else:
            stage, detail = "unknown", changes or "no single stage explains it"
        blames.append(Blame(rid, stage, detail, comparison))
    return blames


def main() -> int:
    blames = blame(load_classified(), load_gold(), load_attributes(), load_gloss_review(), run_oracle())
    counts = Counter(b.stage for b in blames)
    scored = sum(in_scope(c.receipt.receipt_id) for c in load_classified())
    print(f"{len(blames)} of {scored} in-scope receipts differ from the answer key")
    for stage in STAGES:
        if counts[stage]:
            print(f"  {stage}: {counts[stage]}")
    for b in blames:
        print(f"  {b.receipt_id} [{b.stage}] {b.detail}")
    BLAME_PATH.write_text(json.dumps({"mismatched": len(blames), "by_stage": dict(counts),
                                      "receipts": [asdict(b) for b in blames]}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {BLAME_PATH.relative_to(ROOT)}")
    return 1 if counts["unknown"] else 0


if __name__ == "__main__":
    sys.exit(main())
