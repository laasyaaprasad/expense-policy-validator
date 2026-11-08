"""Build experiment 12's blind trap set from lines.json: `uv run python -m experiments.semantic_v4.traps.build`.

lines.json was written by a fresh agent that was not shown the lookup's design, code, memory or the CORD receipts. The
assistant checked every label and drops the lines below before any model or embedding sees the set (README.md).
Receipts are CORD-shaped, with MCC 5812 and no merchant name, four lines to a receipt, in a fixed shuffled order.
"""

import json
import random
from pathlib import Path

from validator.taxonomy import load_taxonomy

HERE = Path(__file__).resolve().parent
DROPPED = {
    "BINTANG ZERO 0.0% 330ML": "ambiguous: a 0.0% beer is a non-alcoholic drink by the taxonomy, but a reviewer could read it as beer",
    "Bintang Radler Lemon": "copies a line of experiment 11's trap set, which the design has seen",
    "MARLBORO ICE BURST": "copies a line of experiment 11's trap set",
    "TOGEL 4D": "copies a line of experiment 11's trap set",
    "PENNE ALLA VODKA": "copies a line of the lookup's curated library",
    "ONION RINGS": "copies a CORD training line (R000–R599)",
}


def build() -> None:
    taxonomy = load_taxonomy()
    lines = [x for x in json.loads((HERE / "lines.json").read_text(encoding="utf-8")) if x["text"] not in DROPPED]
    random.Random(12).shuffle(lines)
    receipts, labels = [], []
    for n in range(0, len(lines), 4):
        rid = f"BLIND-{n // 4 + 1:03d}"
        items = []
        for k, x in enumerate(lines[n:n + 4], start=1):
            lid = f"{rid}-L{k}"
            items.append({"line_id": lid, "description_raw": x["text"], "language": "id", "quantity": 1, "unit_price": 25000,
                          "amount": 25000, "amount_usd": 1.56, "price_recorded": True})
            labels.append({"receipt_id": rid, "line_id": lid, "description_raw": x["text"], "category": x["category"],
                           "restricted": taxonomy[x["category"]], "sure": True, "note": None})
        receipts.append({
            "receipt_id": rid, "employee_id": "E-TRAP-0000", "employee_country": "US", "entity": "GLOBAL",
            "submitted_expense_type": "Meals - Individual", "merchant_name": "(not recorded)", "mcc": "5812",
            "mcc_description": "Eating places and restaurants", "transaction_datetime": "2026-03-02T12:30:00",
            "currency": "IDR", "attendee_count": 1, "trip_nights": 3, "pre_approval_ref": None, "itemized": True,
            "trip_id": "TRIP-TRAP-0000", "fx_rate_to_usd": 6.25e-05, "total_amount": 25000 * len(items),
            "total_amount_usd": round(1.5625 * len(items), 2), "line_items": items,
            "source": {"dataset": "experiment 12 blind trap set", "split": "traps"},
        })
    for name, rows in (("receipts.jsonl", receipts), ("labels.jsonl", labels)):
        (HERE / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(receipts)} receipts, {len(labels)} lines: {sum(x['restricted'] for x in labels)} restricted")


if __name__ == "__main__":
    build()
