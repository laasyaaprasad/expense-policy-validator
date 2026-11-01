"""Hand-built engine inputs for tests. Nothing here reads the answer key."""

from datetime import datetime
from decimal import Decimal

from validator.data import LineItem, ReceiptInput, load_receipts
from validator.engine.money import to_usd
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.rules_config import load_rules
from validator.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy()
CONFIG = load_rules()

# A US employee's individual meal at a restaurant on a two-night trip, in USD, itemized, with nothing else recorded.
RECEIPT_DEFAULTS = {
    "employee_id": "E-TEST",
    "employee_country": "US",
    "submitted_expense_type": "Meals - Individual",
    "merchant_name": "Test Merchant",
    "mcc": "5812",
    "mcc_description": "Eating places and restaurants",
    "transaction_datetime": "2026-04-14T12:00:00",
    "currency": "USD",
    "fx_rate_to_usd": "1.0",
    "attendee_count": None,
    "trip_id": "TRIP-TEST",
    "trip_nights": 2,
    "pre_approval_ref": None,
    "itemized": True,
}
CLASSIFICATION_FIELDS = ("confidence", "recipient_name", "recipient_type", "item_kind")


def line(category: str, amount: str, **options) -> dict:
    """One line: its category and amount in the receipt's currency. Options: quantity, description,
    confidence, recipient_name, recipient_type, item_kind."""
    return {"category": category, "amount": amount, **options}


def receipt(receipt_id: str, *lines: dict, **fields) -> ClassifiedReceipt:
    """A classified receipt. Lines are numbered L1, L2, ... in the order given."""
    values = {**RECEIPT_DEFAULTS, **fields}
    values.setdefault("entity", CONFIG.region_of(values["employee_country"]))
    rate = Decimal(values["fx_rate_to_usd"]) if values["fx_rate_to_usd"] is not None else Decimal(1)
    items, classified = [], {}
    for number, spec in enumerate(lines, start=1):
        line_id = f"{receipt_id}-L{number}"
        quantity = Decimal(spec.get("quantity", 1))
        amount = Decimal(spec["amount"])
        items.append(LineItem(
            line_id=line_id,
            description_raw=spec.get("description", spec["category"]),
            language="en",
            quantity=quantity,
            unit_price=amount / quantity,
            amount=amount,
            amount_usd=to_usd(amount, rate),
        ))
        classified[line_id] = ClassifiedItem(
            line_id=line_id,
            category=spec["category"],
            restricted=TAXONOMY[spec["category"]],
            confidence=spec.get("confidence", 1.0),
            **{key: spec[key] for key in CLASSIFICATION_FIELDS[1:] if key in spec},
        )
    total = sum((item.amount for item in items), Decimal(0))
    values.setdefault("total_amount", total)
    values.setdefault("total_amount_usd", to_usd(total, rate))
    values["transaction_datetime"] = values["transaction_datetime"] and datetime.fromisoformat(values["transaction_datetime"])
    return ClassifiedReceipt(receipt=ReceiptInput(receipt_id=receipt_id, line_items=tuple(items), **values), items=classified)


_RECEIPTS = {r.receipt_id: r for r in load_receipts()}


def from_data(receipt_id: str, **categories: str) -> ClassifiedReceipt:
    """A real receipt from the data, with each line's category written by hand in the test:
    from_data("RCP-027", L1="clothing", L2="clothing")."""
    data = _RECEIPTS[receipt_id]
    items = {}
    for item in data.line_items:
        category = categories[item.line_id.rpartition("-")[2]]
        items[item.line_id] = ClassifiedItem(line_id=item.line_id, category=category, restricted=TAXONOMY[category], confidence=1.0)
    return ClassifiedReceipt(receipt=data, items=items)
