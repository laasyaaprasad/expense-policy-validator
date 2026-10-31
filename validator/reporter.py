"""Turns an engine decision into the brief's output record.

`evidence` is the rule's verbatim policy text, stored in rules.yaml and checked against the documents.
`explanation` is a template filled with the numbers the engine computed. No model writes either:
an auditor can trace every sentence to a rule and a calculation.

`python -m validator.reporter` regenerates schemas/receipt_result.schema.json.
"""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from validator.data import ROOT
from validator.models import ClassifiedReceipt, Decision, Disposition, Finding, Signal
from validator.rules_config import RulesConfig, load_rules

SCHEMA_PATH = ROOT / "schemas" / "receipt_result.schema.json"


# --- the output record. Field names marked "brief" are the brief's and must not be renamed. -------

class ResultLine(BaseModel):
    line_id: str  # brief
    description_raw: str  # brief
    category: str  # brief
    restricted: bool  # brief
    confidence: float  # brief
    recipient_name: str | None = None
    recipient_type: str | None = None
    item_kind: str | None = None
    reason: str | None = None


class ResultException(BaseModel):
    rule_id: str  # brief
    policy_doc: str  # brief
    severity: str  # brief
    line_ids: list[str]  # brief
    evidence: str  # brief
    explanation: str  # brief
    facts: dict[str, Any] = {}


class ResultSignal(BaseModel):
    rule_id: str  # brief
    reason: str  # brief
    policy_doc: str
    line_ids: list[str] = []


class ResultWaiver(BaseModel):
    rule_id: str
    policy_doc: str
    line_ids: list[str]
    reference: str
    explanation: str


class ReceiptResult(BaseModel):
    """One receipt's result. Extra fields are allowed on read; the brief's fields are required."""

    model_config = ConfigDict(extra="allow")

    receipt_id: str  # brief
    disposition: Disposition  # brief
    line_items: list[ResultLine]  # brief
    exceptions: list[ResultException]  # brief
    review_signals: list[ResultSignal]  # brief
    waived: list[ResultWaiver] = []
    not_evaluated: list[dict[str, str]] = []
    rules_evaluated: list[str] = []
    policy_version: str | None = None


# --- explanations ---------------------------------------------------------------------------------

def usd(value) -> str:
    return f"${Decimal(value):,.2f}"


def eur(value) -> str:
    return f"EUR {Decimal(value):,.2f}"


class _Lines:
    """The line descriptions an explanation quotes."""

    def __init__(self, classified: ClassifiedReceipt):
        self.descriptions = {line.line_id: line.description_raw for line in classified.receipt.line_items}

    def __call__(self, line_ids) -> str:
        quoted = [f'"{self.descriptions[line_id]}"' for line_id in line_ids]
        return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _be(line_ids) -> str:
    return "is" if len(line_ids) == 1 else "are"


def _pick(line_ids, one: str, many: str) -> str:
    return one if len(line_ids) == 1 else many


def _reference_note(facts: dict) -> str:
    if facts.get("reference"):
        kind = (facts.get("reference_kind") or "unrecognised reference").replace("_", "-")
        return f"; the recorded reference {facts['reference']} is a {kind}, which does not satisfy this rule"
    return ""


def _meal(f: Finding, lines: _Lines) -> str:
    facts = f.facts
    across = f" across {', '.join(facts['receipts'])}" if len(facts["receipts"]) > 1 else ""
    cap = usd(facts["cap_usd"])
    if "cap_eur" in facts:
        cap = f"{eur(facts['cap_eur'])} ({usd(facts['cap_usd'])} at {facts['euro_rate']})"
    return f"Meal spend on {facts['date']} is {usd(facts['per_person_day_usd'])} per person{across}, over the {cap} daily cap."


def _gift_01(f: Finding, lines: _Lines) -> str:
    parts = [
        f"gifts for {group['recipient']} total {usd(group['total_usd'])}" if group["recipient"]
        else f"{lines(group['line_ids'])} is {usd(group['total_usd'])} for one unnamed recipient"
        for group in f.facts["over_cap"]
    ]
    joined = "; ".join(parts)
    sentence = f"{joined[:1].upper()}{joined[1:]}, over the {usd(f.facts['cap_usd'])} cap per recipient." if parts else ""
    if f.facts["unnamed_line_ids"]:
        ids = f.facts["unnamed_line_ids"]
        unnamed = f"{lines(ids)} {_pick(ids, 'costs', 'each cost')} {usd(f.facts['name_required_from_usd'])} or more and {_pick(ids, 'names', 'name')} no recipient."
        sentence = f"{sentence} {unnamed}".strip()
    return sentence


def _personal(kind: str) -> Callable[[Finding, _Lines], str]:
    def explain(f: Finding, lines: _Lines) -> str:
        if "allowance_usd" in f.facts:
            return (f"{lines(f.line_ids)} {_be(f.line_ids)} {kind}; baggage-delay spend under {f.facts['reference']} is "
                    f"{usd(f.facts['used_usd'])}, over the {usd(f.facts['allowance_usd'])} allowance.")
        return f"{lines(f.line_ids)} {_be(f.line_ids)} {kind}, and no baggage-delay reference is recorded{_reference_note(f.facts)}."
    return explain


def _health_01(f: Finding, lines: _Lines) -> str:
    facts, parts = f.facts, []
    prescription = facts.get("prescription_line_ids", [])
    otc = [line_id for line_id in f.line_ids if line_id not in prescription]
    if prescription:
        parts.append(f"{lines(prescription)} {_be(prescription)} prescription medication, never reimbursable on an expense report.")
    if otc and facts.get("trip_id") is None:
        parts.append(f"{lines(otc)} {_be(otc)} OTC medication on a receipt with no trip record.")
    elif otc:
        parts.append(f"OTC medication on trip {facts['trip_id']} totals {usd(facts['trip_otc_usd'])}, over the {usd(facts['cap_usd'])} cap.")
    return " ".join(parts)


def _laundry_01(f: Finding, lines: _Lines) -> str:
    nights = f.facts["trip_nights"]
    trip = "a receipt with no trip record" if nights is None else f"a {nights}-night trip"
    return f"{lines(f.line_ids)} {_be(f.line_ids)} laundry on {trip}; laundry needs a trip of {f.facts['min_trip_nights']} or more nights."


def _attendees(count) -> str:
    return "no attendee count" if count is None else f"{count} attendee{'s' if count != 1 else ''}"


EXPLAIN: dict[str, Callable[[Finding, _Lines], str]] = {
    "MEAL-01": _meal,
    "REG-02": _meal,
    "REG-03": _meal,
    "MEAL-02": lambda f, lines: f"The meal receipt totals {usd(f.facts['total_usd'])}, above {usd(f.facts['threshold_usd'])}, and records no attendee count.",
    "ALC-01": lambda f, lines: (f"{lines(f.line_ids)} {_be(f.line_ids)} alcohol on a {f.facts['expense_type']} receipt with "
                                f"{_attendees(f.facts['attendee_count'])}; alcohol needs Client Entertainment with two or more attendees."),
    "ALC-02": lambda f, lines: (f"{usd(f.facts['alcohol_usd'])} of alcohol for {f.facts['attendee_count']} attendees is "
                                f"{usd(f.facts['per_attendee_usd'])} each, over the {usd(f.facts['cap_usd'])} cap."),
    "ALC-03": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} alcohol bought at {f.facts['mcc_description']} (MCC {f.facts['mcc']}), which is retail.",
    "REG-01": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} alcohol, which is never reimbursable for {f.facts['entity']} employees.",
    "ENT-01": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} entertainment-venue spend, and no VP approval is recorded{_reference_note(f.facts)}.",
    "ENT-02": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} gambling.",
    "TECH-01": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} personal electronics.",
    "TECH-02": lambda f, lines: ("; ".join(f"{lines([line_id])} costs {usd(price)} per unit" for line_id, price in f.facts["unit_price_usd"].items())
                                 + f", over the {usd(f.facts['cap_usd'])} cap, and no pre-approval is recorded{_reference_note(f.facts)}."),
    "TECH-03": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} mobile phone hardware, which IT procures centrally.",
    "TECH-04": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} {_pick(f.line_ids, 'a software subscription', 'software subscriptions')}, and no manager approval is recorded{_reference_note(f.facts)}.",
    "GIFT-01": _gift_01,
    "GIFT-02": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} {_pick(f.line_ids, 'a gift card or cash equivalent', 'gift cards or cash equivalents')}.",
    "GIFT-03": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} {_pick(f.line_ids, 'a gift', 'gifts')} for an employee, which must go through HR.",
    "GIFT-04": lambda f, lines: f"{lines(f.line_ids)} {_pick(f.line_ids, 'was', 'were')} provided to {', '.join(f.facts['recipients'])}, a government official.",
    "PERS-01": _personal("personal care"),
    "PERS-02": _personal("clothing"),
    "PERS-03": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} tobacco.",
    "HOUSE-01": lambda f, lines: f"{lines(f.line_ids)} {_be(f.line_ids)} household goods.",
    "HEALTH-01": _health_01,
    "LAUNDRY-01": _laundry_01,
    "MCC-01": lambda f, lines: f"MCC {f.facts['mcc']} ({f.facts['mcc_description']}) is a high-risk merchant category.",
    "DOC-01": lambda f, lines: f"The receipt totals {usd(f.facts['total_usd'])}, above {usd(f.facts['threshold_usd'])}, and is not itemized.",
    "DOC-02": lambda f, lines: f"The receipt is missing its {' and '.join(f.facts['missing'])}.",
    "DOC-03": lambda f, lines: (f"Declared as {f.facts['expense_type']}, which covers {', '.join(f.facts['covers'])}, "
                                f"but the items are {', '.join(f.facts['categories'])}."),
    "DOC-04": lambda f, lines: f"The receipt is not in USD and is missing its {' and '.join(f.facts['missing'])}.",
    "REG-04": lambda f, lines: f"The receipt totals {eur(f.facts['total_eur'])}, above {eur(f.facts['threshold_eur'])}, and no VAT number is recorded.",
}


def _signal_reason(s: Signal, lines: _Lines) -> str:
    if s.rule_id == "MCC-02":
        return f"MCC {s.facts['mcc']} ({s.facts['mcc_description']}) is medium risk; context only, not an exception."
    if s.rule_id == "EXC-03":
        scores = ", ".join(f"{lines([line_id])} at {confidence:.2f}" for line_id, confidence in s.facts["confidence"].items())
        return f"Category uncertain for {scores} (threshold {s.facts['threshold']:.2f}); left out of every other rule."
    raise KeyError(f"no reason template for signal {s.rule_id}")


# --- the record -----------------------------------------------------------------------------------

def report(classified: ClassifiedReceipt, decision: Decision, config: RulesConfig | None = None,
           policy_version: str | None = None) -> ReceiptResult:
    config = config or load_rules()
    lines = _Lines(classified)
    return ReceiptResult(
        receipt_id=decision.receipt_id,
        disposition=decision.disposition,
        line_items=[
            ResultLine(description_raw=line.description_raw, **classified.items[line.line_id].model_dump())
            for line in classified.receipt.line_items
        ],
        exceptions=[
            ResultException(
                rule_id=f.rule_id, policy_doc=f.policy_doc, severity=f.severity, line_ids=list(f.line_ids),
                evidence=config.rule(f.rule_id).evidence, explanation=EXPLAIN[f.rule_id](f, lines), facts=f.facts,
            )
            for f in decision.exceptions
        ],
        review_signals=[
            ResultSignal(rule_id=s.rule_id, reason=_signal_reason(s, lines), policy_doc=s.policy_doc, line_ids=list(s.line_ids))
            for s in decision.review_signals
        ],
        waived=[
            ResultWaiver(rule_id=w.rule_id, policy_doc=w.policy_doc, line_ids=list(w.line_ids), reference=w.reference,
                         explanation=f"{w.rule_id} on {lines(w.line_ids)} is satisfied by the recorded reference {w.reference}.")
            for w in decision.waived
        ],
        not_evaluated=[n.model_dump() for n in decision.not_evaluated],
        rules_evaluated=list(decision.rules_evaluated),
        policy_version=policy_version,
    )


def to_json_line(result: ReceiptResult) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)


def schema() -> str:
    return json.dumps(ReceiptResult.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(schema(), encoding="utf-8")
    print(f"Wrote {SCHEMA_PATH.relative_to(ROOT)}")
