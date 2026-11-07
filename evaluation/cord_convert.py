"""Convert CORD v2 receipts (naver-clova-ix/cord-v2, CC-BY-4.0) to this project's receipt schema.

CORD gives real line text, quantities and prices, but no expense context: no merchant name, date,
employee, expense type or MCC. Every receipt therefore gets the same declared, synthetic context
(CONTEXT below, and cord/README.md), and its own employee and trip, so no cross-receipt total joins
two receipts. Each menu item becomes a line, followed by its sub-items (options, toppings, package
contents) as lines of their own. A nameless item has nothing to classify and is dropped.

Standard library only, so it can run isolated on the downloaded file:

    python -I evaluation/cord_convert.py <rows.json> cord/receipts.jsonl
"""

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

IDR_PER_USD = Decimal(16000)
FX_RATE_TO_USD = 1 / IDR_PER_USD  # 0.0000625, exact
CENT = Decimal("0.01")
SPLIT_LETTER = {"train": "R"}  # train and test both start with T; validation stays V, test T
CONTEXT = {
    "employee_country": "US",
    "entity": "GLOBAL",
    "submitted_expense_type": "Meals - Individual",
    "merchant_name": "(not recorded)",
    "mcc": "5812",
    "mcc_description": "Eating places and restaurants",
    "transaction_datetime": "2026-03-02T12:30:00",
    "currency": "IDR",
    "attendee_count": 1,
    "trip_nights": 3,  # a US employee paying in rupiah is travelling; each receipt gets its own trip
    "pre_approval_ref": None,
    "itemized": True,
}
LANGUAGE = "id"  # CORD's language; many lines are English, and the translator glosses those too


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def text(value) -> str | None:
    """CORD sometimes splits one name into a list of words."""
    joined = " ".join(str(v) for v in as_list(value)).strip()
    return joined or None


def parse_idr(value) -> Decimal | None:
    """'16,500', '23.000', 'Rp 29,090', '18 000', '70000.00', '-Rp 34,363' → whole rupiah.

    Rupiah have no minor unit in use, so '.' and ',' are thousands separators, except a trailing
    two-digit '.00' or ',00'.
    """
    s = text(value)
    if s is None:
        return None
    negative = s.lstrip().startswith("-")
    s = re.sub(r"[.,]\d{2}$", "", s.strip())
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    amount = Decimal(digits)
    return -amount if negative else amount


def parse_quantity(value) -> Decimal:
    """'1', '1X', '1.00', '2 X', '1Prs', '*2', '118' → the first number; 1 when there is none."""
    match = re.search(r"\d+(?:[.,]\d+)?", text(value) or "")
    quantity = Decimal(match.group().replace(",", ".")) if match else Decimal(1)
    return quantity if quantity > 0 else Decimal(1)


def usd(amount: Decimal) -> Decimal:
    return (amount * FX_RATE_TO_USD).quantize(CENT, ROUND_HALF_UP)


def line(receipt_id: str, number: int, item: dict) -> dict:
    amount = parse_idr(item.get("price"))
    quantity = parse_quantity(item.get("cnt"))
    amount = amount if amount is not None else Decimal(0)
    return {
        "line_id": f"{receipt_id}-L{number}",
        "description_raw": text(item.get("nm")),
        "language": LANGUAGE,
        "quantity": quantity,
        "unit_price": (amount / quantity).quantize(CENT, ROUND_HALF_UP),
        "amount": amount,
        "amount_usd": usd(amount),
        "price_recorded": item.get("price") is not None,
    }


def convert(row: dict, index: int) -> dict:
    truth = json.loads(row["ground_truth"])
    parse, meta = truth["gt_parse"], truth["meta"]
    receipt_id = f"CORD-{SPLIT_LETTER.get(meta['split'], meta['split'][0].upper())}{meta['image_id']:03d}"
    items, dropped = [], 0
    for menu in as_list(parse.get("menu")):
        for item in [menu, *as_list(menu.get("sub"))]:
            if text(item.get("nm")) is None:
                dropped += 1
                continue
            items.append(line(receipt_id, len(items) + 1, item))
    total = next((parse_idr(t.get("total_price")) for t in as_list(parse.get("total")) if t.get("total_price")), None)
    total_from_lines = total is None
    if total_from_lines:
        total = sum((i["amount"] for i in items), Decimal(0))
    return {
        "receipt_id": receipt_id,
        "employee_id": f"E-CORD-{index:04d}",
        **CONTEXT,
        "trip_id": f"TRIP-CORD-{index:04d}",
        "fx_rate_to_usd": FX_RATE_TO_USD,
        "total_amount": total,
        "total_amount_usd": usd(total),
        "line_items": items,
        # Provenance. The receipt loader ignores it.
        "source": {"dataset": "naver-clova-ix/cord-v2", "split": meta["split"], "image_id": meta["image_id"],
                   "total_from_lines": total_from_lines, "dropped_nameless_items": dropped},
    }


def number(value):
    """Write Decimals as JSON numbers. A float's repr round-trips them exactly at these sizes."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) >= 1 else float(value)
    raise TypeError(f"not JSON serialisable: {value!r}")


def main(argv: list[str]) -> int:
    source, out = Path(argv[0]), Path(argv[1])
    rows = json.loads(source.read_text(encoding="utf-8"))["rows"]
    receipts = [convert(r["row"], r["row_idx"]) for r in rows]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for receipt in receipts:
            f.write(json.dumps(receipt, ensure_ascii=False, default=number) + "\n")
    lines = sum(len(r["line_items"]) for r in receipts)
    dropped = sum(r["source"]["dropped_nameless_items"] for r in receipts)
    print(f"{len(receipts)} receipts, {lines} lines ({dropped} nameless items dropped) → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
