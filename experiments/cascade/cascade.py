"""Putting the cheap stage and the LLM together: which lines escalate, the threshold rule, and cost.

An escalated line takes the LLM's committed answer (PLAN.md, Escalation). A line the cheap stage
keeps gets its category, `restricted` from the taxonomy, no item kind or recipient (the gates send
those lines to the LLM), and confidence 1.0, so EXC-03 applies only through the LLM's confidence.
"""

import math
from dataclasses import dataclass

import numpy as np

from experiments.cascade.cheap import RECIPIENT, CheapLabel
from validator.classifier import TAXONOMY
from validator.data import ReceiptInput
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.normaliser import normalise

DEV_MIN_ACCURACY = 0.95  # PLAN.md, threshold rule
LIST_PRICE = {"in": 0.21e-6, "out": 0.84e-6}  # Fireworks' list price for the model, $ per token


@dataclass(frozen=True)
class Arm:
    name: str
    scorer: str  # "embedding" or "tfidf"
    escalates: bool  # False: the cheap stage answers every line
    conservative: bool = False  # also escalate every line the cheap stage calls restricted


def escalation_reason(text: str, label: CheapLabel, threshold: float, conservative: bool) -> str | None:
    """Why this line goes to the LLM, or None if the cheap stage keeps it."""
    if label.category == "other":
        return "cheap stage says other"
    if RECIPIENT.search(text):
        return "names a person"
    if conservative and TAXONOMY[label.category]:
        return "restricted"
    if label.margin < threshold:
        return "low margin"
    return None


def choose_threshold(dev: list[tuple[str, CheapLabel, str]], conservative: bool) -> tuple[float, list[dict]]:
    """PLAN.md's rule: the lowest candidate at which the dev lines the cheap stage keeps have no wrong
    restricted flag and at least 95% category accuracy; infinity (everything escalates) if none does.
    `dev` holds (text, leave-one-out label, expected category) per dev line."""
    candidates = sorted({0.0} | {label.margin for _, label, _ in dev})
    table = []
    for threshold in candidates:
        kept = [(label, want) for text, label, want in dev if escalation_reason(text, label, threshold, conservative) is None]
        right = sum(label.category == want for label, want in kept)
        flag_errors = sum(TAXONOMY[label.category] != TAXONOMY[want] for label, want in kept)
        accuracy = right / len(kept) if kept else None
        ok = bool(kept) and flag_errors == 0 and accuracy >= DEV_MIN_ACCURACY
        table.append({"threshold": threshold, "kept": len(kept), "accuracy": accuracy, "restricted_errors": flag_errors, "qualifies": ok})
        if ok:
            return threshold, table
    return math.inf, table


@dataclass(frozen=True)
class Routed:
    classified: list[ClassifiedReceipt]
    source: dict[str, str]  # line ID -> "cheap", "llm", or "fixed" (non-itemized: no stage runs)
    reason: dict[str, str | None]  # line ID -> why it escalated


def route(receipts: list[ReceiptInput], llm: dict[str, ClassifiedItem], cheap: dict[str, CheapLabel],
          arm: Arm, threshold: float) -> Routed:
    classified, source, reason = [], {}, {}
    for receipt in receipts:
        items = {}
        for line in receipt.line_items:
            if not receipt.itemized:
                items[line.line_id], source[line.line_id], reason[line.line_id] = llm[line.line_id], "fixed", None
                continue
            label = cheap[line.line_id]
            why = escalation_reason(normalise(line.description_raw), label, threshold, arm.conservative) if arm.escalates else None
            reason[line.line_id] = why
            if why is not None:
                items[line.line_id], source[line.line_id] = llm[line.line_id], "llm"
            else:
                items[line.line_id] = ClassifiedItem(line_id=line.line_id, category=label.category,
                                                     restricted=TAXONOMY[label.category], confidence=1.0,
                                                     reason=f"cheap stage ({arm.scorer}), margin {label.margin:.4f}")
                source[line.line_id] = "cheap"
        classified.append(ClassifiedReceipt(receipt=receipt, items=items))
    return Routed(classified, source, reason)


# --- cost --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CallTokens:
    """Tokens for one call as a fixed part plus a part per line, fitted from the spend ledger."""
    fixed_in: float
    per_line_in: float
    fixed_out: float
    per_line_out: float

    def usd(self, lines: int) -> float:
        return (LIST_PRICE["in"] * (self.fixed_in + self.per_line_in * lines)
                + LIST_PRICE["out"] * (self.fixed_out + self.per_line_out * lines))


def fit_call_tokens(calls: list[tuple[int, int, int]]) -> CallTokens:
    """Least squares over (lines, prompt tokens, completion tokens) per call."""
    lines = np.array([c[0] for c in calls], dtype=float)
    design = np.column_stack([np.ones_like(lines), lines])
    (a, b), *_ = np.linalg.lstsq(design, np.array([c[1] for c in calls], dtype=float), rcond=None)
    (c, d), *_ = np.linalg.lstsq(design, np.array([c[2] for c in calls], dtype=float), rcond=None)
    return CallTokens(float(a), float(b), float(c), float(d))


def llm_cost(receipts: list[ReceiptInput], source: dict[str, str], classify: CallTokens, translate_usd: float) -> float:
    """What a run would cost on the LLM side: one classify call per receipt with any escalated line,
    sized to those lines, plus a translation call when one of them isn't in English."""
    total = 0.0
    for receipt in receipts:
        sent = [line for line in receipt.line_items if source[line.line_id] == "llm"]
        if not sent:
            continue
        total += classify.usd(len(sent))
        if any(line.language != "en" for line in sent):
            total += translate_usd
    return total
