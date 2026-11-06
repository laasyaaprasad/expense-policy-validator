"""The extraction prompt: what the model is told about the config schema, and what it sees per rule.

It is given the field definitions, the allowed values, one made-up example, the rule's own text and
the names of the parameters its Python check reads (the code's interface, never the values). It is
never given rules.yaml's values, the answer key or any receipt (PLAN.md).
"""

import json
from typing import get_args

from validator.classifier import TAXONOMY
from validator.models import ItemKind, RecipientType
from validator.policies import Rule
from validator.rules_config import RulesConfig

PROMPT_VERSION = "policy-config-v1"
REFERENCE_KINDS = ("pre_approval", "vp_approval", "manager_approval", "baggage_delay")
SEVERITIES = ("critical", "high", "medium", "low")
MCC_TIERS = ("high", "medium", "low")


def allowed_values(shared: RulesConfig) -> dict:
    """The vocabularies the fields draw from. Taken from the taxonomy, the models and the shared
    tables (which the experiment copies), never from any rule's own entry."""
    return {
        "categories": sorted(TAXONOMY),
        "item_kinds": list(get_args(ItemKind)),
        "recipient_types": list(get_args(RecipientType)),
        "expense_types": sorted(shared.expense_type_covers),
        "regions": sorted(shared.regions),
        "mcc_risk": list(MCC_TIERS),
        "reference_kinds": list(REFERENCE_KINDS),
        "severities": list(SEVERITIES),
    }


EXAMPLE = {
    "rule": {"id": "PARK-01", "title": "Airport parking", "text": "**PARK-01 — Airport parking.** Airport parking is "
             "reimbursable up to $20 a day for employees on a trip. Parking above the daily limit is a **low** exception "
             "unless a manager approval is recorded.", "parameter_names": ["daily_cap_usd"]},
    "answer": {"kind": "exception", "severity": "low", "scope": "items", "params": {"daily_cap_usd": 20},
               "applies_to": {"categories": ["ground_transport"]}, "overrides": [], "line_overrides": [], "replaces": [],
               "requires_reference": "manager_approval",
               "evidence": "Airport parking is reimbursable up to $20 a day for employees on a trip."},
}


def system_prompt(shared: RulesConfig) -> str:
    values = allowed_values(shared)
    return f"""You turn one written expense-policy rule into a structured config entry for a deterministic policy engine. The engine applies every rule in code; you only describe the rule.

Reply with one json object with exactly these keys:
- kind: "exception" if breaking the rule raises an exception with a severity; "review_signal" if it sends the receipt to review without a severity; "mechanic" if it describes how the engine itself works (how dispositions are computed, how references or evidence work, how amounts are converted, how a rule is to be read) and is never checked against a receipt on its own.
- severity: for an exception, the severity the text states, one of {values["severities"]}. If you are given severity_cases, a json object from each case name to its severity. Null for a review_signal or mechanic.
- scope: for an exception, "items" if it concerns specific line items, "receipt" if it concerns the receipt as a whole. Null otherwise.
- params: a json object with exactly the parameter names you are given, each set to the number the text states, in the unit the name implies. An empty object if you are given none.
- applies_to: when the rule can apply to a receipt, used to decide which rules to check. A rule is checked when its region passes and any one of the other conditions matches. Keys, all optional:
  - regions: list from {values["regions"]}; set it only when the rule applies to employees of those entities alone. Empty means every region.
  - always: true if the rule can apply to any receipt.
  - categories: line-item categories that trigger it, from {values["categories"]}.
  - item_kinds: from {values["item_kinds"]}.
  - recipient_types: from {values["recipient_types"]}.
  - names_recipient: true if it applies whenever a line names who the item is for.
  - expense_types: declared expense types that trigger it, from {values["expense_types"]}.
  - mcc_risk: merchant risk tiers that trigger it, from {values["mcc_risk"]}.
  For a mechanic, an empty object.
- overrides: IDs of rules this rule replaces entirely wherever it applies (a regional rule replacing a global one).
- line_overrides: IDs of rules this rule replaces only for the line items it cites.
- replaces: IDs of rules this rule may replace on a receipt where it is raised.
- requires_reference: the kind of recorded reference that satisfies this rule when present, one of {values["reference_kinds"]}, if the text says one does; otherwise null.
- evidence: the sentence or two that state the rule, copied word for word from the rule text, without markdown. Do not paraphrase.

Use only rule IDs that appear in the text you are given. Here is a made-up example, not from these policies:
{json.dumps(EXAMPLE, ensure_ascii=False)}"""


def user_message(rule: Rule, parameter_names: list[str], severity_cases: list[str], related: list[Rule]) -> str:
    payload = {"rule": {"id": rule.id, "title": rule.title, "document": rule.doc, "section": rule.section,
                        "text": rule.text, "parameter_names": parameter_names}}
    if severity_cases:
        payload["rule"]["severity_cases"] = severity_cases
    if related:
        payload["related_rules"] = [{"id": r.id, "title": r.title, "document": r.doc, "text": r.text} for r in related]
    return json.dumps(payload, ensure_ascii=False)


def related_rules(rule_id: str, policies: dict[str, Rule]) -> list[Rule]:
    """Variant 2's retrieval: every rule this one cites and every rule that cites it, in ID order."""
    cited = set(policies[rule_id].mentions)
    citing = {other.id for other in policies.values() if rule_id in other.mentions}
    return [policies[i] for i in sorted((cited | citing) - {rule_id}) if i in policies]
