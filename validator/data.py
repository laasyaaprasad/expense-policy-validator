"""Receipt input models: everything the pipeline may see, and nothing from the answer key."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RECEIPTS_PATH = DATA_DIR / "receipts.jsonl"
POLICY_DIR = DATA_DIR / "policy-docs"
TAXONOMY_PATH = DATA_DIR / "category-taxonomy.md"


class LineItem(BaseModel):
    # extra="ignore" drops each line's `labels` (its gold category) on load.
    model_config = ConfigDict(extra="ignore", frozen=True)

    line_id: str
    description_raw: str
    language: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    amount_usd: Decimal


class ReceiptInput(BaseModel):
    # extra="ignore" drops the receipt's `labels` and `notes` on load.
    model_config = ConfigDict(extra="ignore", frozen=True)

    receipt_id: str
    employee_id: str
    employee_country: str
    entity: str
    submitted_expense_type: str
    # Optional so synthetic receipts can test DOC-02 (legibility) and DOC-04 (currency).
    merchant_name: str | None
    mcc: str
    mcc_description: str
    transaction_datetime: datetime | None
    currency: str | None
    fx_rate_to_usd: Decimal | None
    total_amount: Decimal | None
    total_amount_usd: Decimal | None
    attendee_count: int | None
    trip_id: str | None
    trip_nights: int | None
    pre_approval_ref: str | None
    itemized: bool
    line_items: tuple[LineItem, ...]


def read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file, reading every JSON float as an exact Decimal."""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line, parse_float=Decimal) for line in f if line.strip()]


def load_receipts(path: Path = RECEIPTS_PATH) -> list[ReceiptInput]:
    return [ReceiptInput.model_validate(row) for row in read_jsonl(path)]
