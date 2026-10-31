"""The answer key. Only evaluation code may import this module."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from validator.data import RECEIPTS_PATH, read_jsonl

# The timing receipts (TIME-01 to TIME-04) are out of scope on this track and not scored.
OUT_OF_SCOPE = frozenset({"RCP-020", "RCP-021", "RCP-029", "RCP-030"})


class GoldItem(BaseModel):
    category: str
    restricted: bool


class GoldException(BaseModel):
    rule_id: str
    policy_doc: str
    severity: str
    line_ids: tuple[str, ...]
    reason: str


class GoldSignal(BaseModel):
    rule_id: str
    policy_doc: str
    reason: str


class GoldReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str
    disposition: str
    exceptions: tuple[GoldException, ...]
    review_signals: tuple[GoldSignal, ...]
    items: dict[str, GoldItem]  # line_id → its correct label
    notes: str


def in_scope(receipt_id: str) -> bool:
    return receipt_id not in OUT_OF_SCOPE


def load_gold(path: Path = RECEIPTS_PATH) -> dict[str, GoldReceipt]:
    """Receipt ID → its correct answer."""
    gold = {}
    for row in read_jsonl(path):
        labels = row["labels"]
        gold[row["receipt_id"]] = GoldReceipt(
            receipt_id=row["receipt_id"],
            disposition=labels["disposition"],
            exceptions=labels["expected_exceptions"],
            review_signals=labels["review_signals"],
            items={item["line_id"]: item["labels"] for item in row["line_items"]},
            notes=row["notes"],
        )
    return gold
