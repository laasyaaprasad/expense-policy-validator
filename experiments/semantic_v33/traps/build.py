"""Build experiment 11's look-alike trap set: `uv run python -m experiments.semantic_v33.traps.build`.

Written and labelled by the assistant before any model or embedding saw these lines (see ../PLAN.md). Two kinds:
- **Restricted look-alikes:** restricted items written to resemble the lookup's memory, which is almost all
  Indonesian café and restaurant food ("Night cream" against "Cream [L]", "Pillow case" against "Pillow Choco").
  Some use brand or local names that no word list holds.
- **Safe look-alikes:** food and drink whose names sound restricted ("Ginger ale", "Chocolate cigar").

Every receipt is CORD-shaped, with MCC 5812 and no merchant name, so the merchant gives the lookup no help: the
hardest case. Lines are grouped four to a receipt in a fixed shuffled order. All labels are sure.
"""

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent

RESTRICTED = {
    "personal_care": [
        "Night cream Aloe Vera", "Hand cream Shea Butter", "Body lotion Milk Honey", "Lemon face wash",
        "Green tea shampoo", "Strawberry lip balm", "Vanilla body mist", "Coffee body scrub",
        "Charcoal peel off mask", "Rose water toner", "Krim malam", "Sabun mandi susu", "Cocoa butter body cream",
        "Pond's Age Miracle", "Wardah Lightening Serum", "Biore UV Aqua Rich", "Hair tonic Ginseng", "Cotton buds 100pcs",
    ],
    "alcohol": [
        "Bir Bintang Botol", "Bintang Radler Lemon", "Soju Peach", "Makgeolli Original", "Lychee martini",
        "Mango margarita", "Coconut rum", "Arak Bali", "Anggur Merah Orang Tua", "Guinness Draught",
        "Hite Jinro Grapefruit", "Baileys Irish Cream", "Strawberry daiquiri", "Hot sake", "Jagerbomb",
    ],
    "tobacco": [
        "Sampoerna Mild 16", "Gudang Garam Filter", "Rokok Djarum Super", "Relx pods Mango", "Grape shisha",
        "Marlboro Ice Burst",
    ],
    "personal_electronics": [
        "Bluetooth earbuds", "Power bank 10000mAh", "Selfie stick tripod", "Smart band M6", "Mini speaker portable",
    ],
    "household_goods": [
        "Pillow case brown", "Bantal leher", "Scented candle Vanilla", "Tissue roll 10s", "Lemon dish soap",
        "Glass mug 350ml",
    ],
    "clothing": ["Kaos polos hitam", "Sandal jepit", "Topi baseball", "Kaos kaki 3 pasang", "Batik shirt"],
    "gift": ["Bouquet mawar merah", "Parcel buah lebaran", "Kartu ucapan"],
    "gift_card": ["Voucher belanja 100rb", "Steam Wallet 60000"],
    "gambling": ["Togel 4D", "Lottery scratch card", "Poker chips set"],
    "jewelry": ["Gelang perak", "Cincin emas 2gr", "Kalung mutiara"],
}

SAFE = {
    "food_beverage": [
        "Beer battered fish", "Rum raisin gelato", "Ginger ale", "Chocolate cigar", "Onion rings large",
        "A&W root beer", "Wine gums", "Cream of mushroom soup", "Ice cream sandwich", "Shirley Temple", "Virgin mojito",
        "Cocktail sausage", "Coconut water", "Tiramisu", "Pillow bread Cheese", "Candy necklace", "Soda gembira",
        "Bubble milk tea", "Teh botol sosro", "Lemon tea jelly", "Sake-glazed salmon", "Bir pletok",
    ],
}


def build() -> None:
    lines = [(t, c, True) for c, ts in RESTRICTED.items() for t in ts] + [(t, c, False) for c, ts in SAFE.items() for t in ts]
    random.Random(11).shuffle(lines)
    receipts, labels = [], []
    for n in range(0, len(lines), 4):
        rid = f"TRAP-{n // 4 + 1:03d}"
        items = []
        for k, (text, category, restricted) in enumerate(lines[n:n + 4], start=1):
            lid = f"{rid}-L{k}"
            items.append({"line_id": lid, "description_raw": text, "language": "id", "quantity": 1, "unit_price": 25000,
                          "amount": 25000, "amount_usd": 1.56, "price_recorded": True})
            labels.append({"receipt_id": rid, "line_id": lid, "description_raw": text, "category": category,
                           "restricted": restricted, "sure": True, "note": None})
        receipts.append({
            "receipt_id": rid, "employee_id": "E-TRAP-0000", "employee_country": "US", "entity": "GLOBAL",
            "submitted_expense_type": "Meals - Individual", "merchant_name": "(not recorded)", "mcc": "5812",
            "mcc_description": "Eating places and restaurants", "transaction_datetime": "2026-03-02T12:30:00",
            "currency": "IDR", "attendee_count": 1, "trip_nights": 3, "pre_approval_ref": None, "itemized": True,
            "trip_id": "TRIP-TRAP-0000", "fx_rate_to_usd": 6.25e-05, "total_amount": 25000 * len(items),
            "total_amount_usd": round(1.5625 * len(items), 2), "line_items": items,
            "source": {"dataset": "experiment 11 trap set", "split": "traps"},
        })
    for name, rows in (("receipts.jsonl", receipts), ("labels.jsonl", labels)):
        (HERE / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(receipts)} receipts, {len(labels)} lines: {sum(r['restricted'] for r in labels)} restricted")


if __name__ == "__main__":
    build()
