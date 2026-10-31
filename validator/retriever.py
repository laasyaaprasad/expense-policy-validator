"""Selects the rules that could apply to a receipt, from the `applies_to` conditions in rules.yaml.

No embeddings and no vector store: the policies are 41 short rules with explicit scopes, so a
structured index is exact and every selection can be explained. A rule is retrieved when the
receipt's region passes and any one of its other conditions matches. Rules that a matched rule
cites by ID are then added (one hop, not from always-on rules), with the region applied again.

Engine mechanics (EXC-01 to 04, REG-05, MCC-03) always apply and are never retrieved.
"""

from dataclasses import dataclass

from validator.models import ClassifiedReceipt
from validator.policies import Rule, load_policies
from validator.rules_config import AppliesTo, RuleSpec, RulesConfig, load_rules


@dataclass(frozen=True)
class Retrieval:
    receipt_id: str
    rule_ids: tuple[str, ...]  # in rules.yaml order
    reasons: dict[str, str]  # rule ID → the condition that matched, or the rule that cited it


def _match(applies: AppliesTo, classified: ClassifiedReceipt, config: RulesConfig) -> str | None:
    """The first condition of `applies_to` the receipt meets, described for the log."""
    receipt, items = classified.receipt, classified.items.values()
    checks = [
        (applies.always, "always"),
        (set(applies.categories) & {item.category for item in items}, "category"),
        (set(applies.item_kinds) & {item.item_kind for item in items}, "item kind"),
        (set(applies.recipient_types) & {item.recipient_type for item in items}, "recipient type"),
        (applies.names_recipient and any(item.recipient_name for item in items), "a line names its recipient"),
        (receipt.submitted_expense_type in applies.expense_types, f"expense type {receipt.submitted_expense_type}"),
        (config.mcc_tier(receipt.mcc) in applies.mcc_risk, f"MCC {receipt.mcc} is {config.mcc_tier(receipt.mcc)} risk"),
    ]
    for hit, label in checks:
        if hit:
            return f"{label} {', '.join(sorted(hit))}" if isinstance(hit, set) else label
    return None


def retrieve(classified: ClassifiedReceipt, config: RulesConfig | None = None,
             policies: dict[str, Rule] | None = None) -> Retrieval:
    config = config or load_rules()
    policies = policies or load_policies()
    region = config.region_of(classified.receipt.employee_country)
    candidates = {rule.id: rule for rule in config.rules if rule.kind != "mechanic"}

    def in_region(rule: RuleSpec) -> bool:
        return not rule.applies_to.regions or region in rule.applies_to.regions

    reasons = {}
    for rule in candidates.values():
        if in_region(rule) and (why := _match(rule.applies_to, classified, config)):
            reasons[rule.id] = why
    # Rules that apply to every receipt cite others only as examples (DOC-03 names TECH-01), so
    # citations are followed only from rules this receipt matched on something specific.
    for rule_id in [rule_id for rule_id, why in reasons.items() if why != "always"]:
        for cited in sorted(policies[rule_id].mentions):
            if cited in candidates and cited not in reasons and in_region(candidates[cited]):
                reasons[cited] = f"cited by {rule_id}"
    return Retrieval(
        receipt_id=classified.receipt.receipt_id,
        rule_ids=tuple(rule_id for rule_id in candidates if rule_id in reasons),
        reasons=reasons,
    )
