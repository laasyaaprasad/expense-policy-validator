"""What the classifier hands the policy engine, and what the engine decides."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from validator.data import ReceiptInput

# Who a line is for, read from the line text (gifts-and-awards-policy.md).
RecipientType = Literal["client", "employee", "government_official"]
# Items the policies name that have no category of their own; they are `other` (category-taxonomy.md).
ItemKind = Literal["entertainment_venue", "mobile_phone", "prescription_medication"]
Disposition = Literal["flag", "review", "pass"]


class ClassifiedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    line_id: str
    category: str
    restricted: bool
    confidence: float
    recipient_name: str | None = None
    recipient_type: RecipientType | None = None
    item_kind: ItemKind | None = None
    reason: str | None = None  # the classifier's one-line justification; `other` items must explain themselves


class ClassifiedReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt: ReceiptInput
    items: dict[str, ClassifiedItem]  # line_id → its classification


class Finding(BaseModel):
    """An exception. `facts` holds the computed numbers the reporter quotes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    policy_doc: str
    severity: str
    line_ids: tuple[str, ...]
    facts: dict[str, Any] = {}


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    policy_doc: str
    line_ids: tuple[str, ...] = ()
    facts: dict[str, Any] = {}


class Waiver(BaseModel):
    """A rule that would have raised an exception, satisfied by a recorded reference (EXC-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    policy_doc: str
    line_ids: tuple[str, ...]
    reference: str


class NotEvaluated(BaseModel):
    """A rule the engine could not apply because the receipt lacks what it needs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    reason: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    disposition: Disposition
    exceptions: tuple[Finding, ...]
    review_signals: tuple[Signal, ...]
    waived: tuple[Waiver, ...] = ()
    not_evaluated: tuple[NotEvaluated, ...] = ()
    rules_evaluated: tuple[str, ...] = ()
