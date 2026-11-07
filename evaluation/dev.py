"""The dev set (dev/trap_items.jsonl): hand-written items for prompt work and the EXC-03 threshold.

Never built from receipts or notes. `make dev` classifies it three ways and writes outputs/dev/:
- the classifier as configured, scored on category, item kind and recipient;
- a fresh repeat of the same requests, to measure how often sampling flips an answer;
- the same items without merchant and expense type (the merchant ablation).
Then it applies the pre-registered threshold rule in NOTES.md and prints the result.
"""

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from evaluation.metrics import recipient_matches
from validator.classifier import Classification, classify
from validator.data import ROOT, LineItem, ReceiptInput, read_jsonl
from validator.llm import LLM
from validator.lookup import default_lookup
from validator.pipeline import needs_model
from validator.translate import translate_all

DEV_PATH = ROOT / "dev" / "trap_items.jsonl"
OUT_DIR = ROOT / "outputs" / "dev"
CANDIDATE_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
DEFAULT_THRESHOLD = 0.5  # DECISIONS.md section 4, used when no candidate does better


def load_dev() -> tuple[list[ReceiptInput], dict[str, dict]]:
    """Dev receipts (amounts and dates are placeholders: only the text matters here) and the
    expected answer per line ID."""
    receipts, expected = [], {}
    for row in read_jsonl(DEV_PATH):
        lines = []
        for number, spec in enumerate(row["lines"], start=1):
            line_id = f"{row['receipt_id']}-L{number}"
            lines.append(LineItem(line_id=line_id, description_raw=spec["text"], language=spec["language"],
                                  quantity=Decimal(1), unit_price=Decimal(10), amount=Decimal(10), amount_usd=Decimal(10)))
            expected[line_id] = {"recipient_name": None, "recipient_type": None, "item_kind": None, **spec["expected"],
                                 "trap": spec.get("trap")}
        receipts.append(ReceiptInput(
            receipt_id=row["receipt_id"], employee_id="E-DEV", employee_country="US", entity="GLOBAL",
            submitted_expense_type=row["submitted_expense_type"], merchant_name=row["merchant_name"],
            mcc=row["mcc"], mcc_description=row["mcc_description"], transaction_datetime=datetime(2026, 1, 1, 12),
            currency="USD", fx_rate_to_usd=Decimal(1), total_amount=Decimal(10) * len(lines),
            total_amount_usd=Decimal(10) * len(lines), attendee_count=None, trip_id=None, trip_nights=None,
            pre_approval_ref=None, itemized=True, line_items=tuple(lines),
        ))
    return receipts, expected


@dataclass
class Scored:
    line_id: str
    text: str
    expected: str
    got: str
    confidence: float
    category_ok: bool
    kind_ok: bool
    recipient_ok: bool
    trap: str | None


def score(classifications: list[Classification], expected: dict[str, dict]) -> list[Scored]:
    rows = []
    for c in classifications:
        for line in c.receipt.receipt.line_items:
            item, want = c.receipt.items[line.line_id], expected[line.line_id]
            rows.append(Scored(
                line_id=line.line_id, text=line.description_raw, expected=want["category"], got=item.category,
                confidence=item.confidence, category_ok=item.category == want["category"],
                kind_ok=item.item_kind == want["item_kind"],
                recipient_ok=recipient_matches(item.recipient_name, item.recipient_type, want["recipient_name"], want["recipient_type"]),
                trap=want["trap"],
            ))
    return rows


def choose_threshold(rows: list[Scored]) -> tuple[float, dict[float, int]]:
    """The pre-registered rule: score(t) = wrong items below t minus right items below t. Take the
    best t; on a tie, the lower t (fewer signals). If no t scores above zero, keep the default."""
    scores = {t: sum(not r.category_ok for r in rows if r.confidence < t) - sum(r.category_ok for r in rows if r.confidence < t)
              for t in CANDIDATE_THRESHOLDS}
    best = max(scores.values())
    if best <= 0:
        return DEFAULT_THRESHOLD, scores
    return min(t for t, s in scores.items() if s == best), scores


def main() -> int:
    llm = LLM.or_offline()
    receipts, expected = load_dev()
    lookup = default_lookup()
    glosses = translate_all(needs_model(receipts, lookup), llm)
    runs = {
        "configured": [classify(r, glosses, llm, lookup=lookup) for r in receipts],
        "repeat": [classify(r, glosses, llm, run="repeat", lookup=lookup) for r in receipts],
        "no_context": [classify(r, glosses, llm, context=False, lookup=lookup) for r in receipts],
    }
    scored = {name: score(cs, expected) for name, cs in runs.items()}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in scored.items():
        with (OUT_DIR / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    main_rows = scored["configured"]
    n = len(main_rows)
    print(f"{n} dev items")
    for name, rows in scored.items():
        print(f"  {name:11} category {sum(r.category_ok for r in rows)}/{n}  item kind {sum(r.kind_ok for r in rows)}/{n}  recipient {sum(r.recipient_ok for r in rows)}/{n}")
    flips = [a.line_id for a, b in zip(scored["configured"], scored["repeat"]) if a.got != b.got]
    print(f"  answers that flipped on a fresh repeat: {len(flips)}/{n} {flips}")
    print("\nconfidence, lowest first:")
    for row in sorted(main_rows, key=lambda r: r.confidence)[:12]:
        print(f"  {row.confidence:.4f}  {'ok ' if row.category_ok else 'BAD'}  {row.line_id}  {row.text!r} → {row.got} (want {row.expected})")
    wrong = [r for r in main_rows if not (r.category_ok and r.kind_ok and r.recipient_ok)]
    print("\nerrors:" if wrong else "\nno errors")
    for row in wrong:
        print(f"  {row.line_id} {row.text!r}: got {row.got}, want {row.expected}; kind ok {row.kind_ok}; recipient ok {row.recipient_ok}")
    threshold, scores = choose_threshold(main_rows)
    print(f"\nthreshold scores {scores} → chosen threshold {threshold}")
    (OUT_DIR / "threshold.json").write_text(json.dumps({"scores": {str(k): v for k, v in scores.items()}, "chosen": threshold}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
