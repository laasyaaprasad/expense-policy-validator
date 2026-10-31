"""The policy engine: plain, deterministic code. The same receipts always produce the same decisions.

Pass 1 views every receipt and builds the cross-receipt totals (context.py). Pass 2 decides each
receipt in order: regional overrides, then each remaining rule on its own, then carve-outs, EXC-03
for uncertain items, and EXC-01 for the disposition.
"""

from collections import defaultdict
from collections.abc import Collection, Mapping

from validator.engine.context import Context, NotEvaluable, ReceiptView, build_context, line_order
from validator.engine.disposition import disposition
from validator.engine.rules import CHECKS
from validator.models import ClassifiedReceipt, Decision, Finding, NotEvaluated, Signal, Waiver
from validator.rules_config import RuleSpec, RulesConfig, load_rules

# Items below this confidence raise EXC-03 instead of being judged. Chosen on the dev set by the rule
# pre-registered in NOTES.md (outputs/dev/threshold.json): every dev item scored 0.96 or above and
# all were right, so no candidate did better than the 0.5 default. Never tuned on the receipts.
CONFIDENCE_THRESHOLD = 0.5


def evaluate(
    receipts: list[ClassifiedReceipt],
    config: RulesConfig | None = None,
    retrieved: Mapping[str, Collection[str]] | None = None,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> list[Decision]:
    """Decide every receipt. `retrieved` maps a receipt ID to the rule IDs the retriever chose;
    without it every rule is considered. Decisions come back in receipt-ID order."""
    config = config or load_rules()
    views = [ReceiptView(receipt, config, threshold) for receipt in receipts]
    ctx = build_context(views)
    return [
        decide(view, ctx, None if retrieved is None else retrieved[view.receipt_id])
        for view in sorted(views, key=lambda view: view.receipt_id)
    ]


def _region_applies(rule: RuleSpec, r: ReceiptView) -> bool:
    return not rule.applies_to.regions or r.region in rule.applies_to.regions


def _active_rules(r: ReceiptView, selected: Collection[str] | None) -> list[RuleSpec]:
    """Regional rules override the global rules they name, for employees of that region.
    Overrides are resolved over every rule, so a retriever that misses the regional rule
    cannot bring the global one back."""
    applicable = [rule for rule in r.config.rules if rule.kind != "mechanic" and _region_applies(rule, r)]
    overridden = {target for rule in applicable for target in rule.overrides}
    active = [rule for rule in applicable if rule.id not in overridden and (selected is None or rule.id in selected)]
    # A rule that replaces another on the lines it cites (GIFT-04 over GIFT-01) runs first.
    return sorted(active, key=lambda rule: not rule.line_overrides)


def _check_evidence(rule: RuleSpec, line_ids: tuple[str, ...]) -> None:
    """EXC-04: item rules cite their items; whole-receipt rules cite none."""
    if rule.scope == "receipt" and line_ids:
        raise ValueError(f"{rule.id} is about the whole receipt but cited {line_ids}")
    if rule.scope == "items" and not line_ids:
        raise ValueError(f"{rule.id} is about items but cited none")


def decide(r: ReceiptView, ctx: Context, selected: Collection[str] | None = None) -> Decision:
    config = r.config
    order = {rule.id: index for index, rule in enumerate(config.rules)}
    hidden: dict[str, set[str]] = defaultdict(set)  # rule ID → lines another rule has taken over
    exceptions, signals, waived, not_evaluated = [], [], [], []
    active = _active_rules(r, selected)

    for rule in active:
        view = r.without(hidden[rule.id]) if hidden[rule.id] else r
        try:
            outcome = CHECKS[rule.id](rule, view, ctx)
        except NotEvaluable as reason:
            not_evaluated.append(NotEvaluated(rule_id=rule.id, reason=str(reason)))
            continue
        if outcome is None:
            continue
        line_ids = tuple(sorted(outcome.line_ids, key=line_order))
        for target in rule.line_overrides:
            hidden[target] |= set(line_ids)
        if outcome.waived_by is not None:
            waived.append(Waiver(rule_id=rule.id, policy_doc=rule.doc, line_ids=line_ids, reference=outcome.waived_by))
        elif rule.kind == "review_signal":
            signals.append(Signal(rule_id=rule.id, policy_doc=rule.doc, facts=outcome.facts))
        else:
            _check_evidence(rule, line_ids)
            severity = outcome.severity or rule.severity
            if not isinstance(severity, str):
                raise ValueError(f"{rule.id} has several severities and the check chose none")
            exceptions.append(Finding(rule_id=rule.id, policy_doc=rule.doc, severity=severity, line_ids=line_ids, facts=outcome.facts))

    if r.uncertain:
        # EXC-03: a review signal, not an exception; the items were already left out of every rule above.
        confidences = {line_id: r.items[line_id].confidence for line_id in r.uncertain}
        signals.append(Signal(rule_id="EXC-03", policy_doc=config.rule("EXC-03").doc, line_ids=r.uncertain,
                              facts={"confidence": confidences, "threshold": r.threshold,
                                     "categories": {i: r.items[i].category for i in r.uncertain}}))

    by_rule = lambda finding: order.get(finding.rule_id, len(order))
    exceptions.sort(key=by_rule)
    signals.sort(key=by_rule)
    waived.sort(key=by_rule)
    not_evaluated.sort(key=by_rule)
    return Decision(
        receipt_id=r.receipt_id,
        disposition=disposition((finding.severity for finding in exceptions), bool(signals)),
        exceptions=tuple(exceptions),
        review_signals=tuple(signals),
        waived=tuple(waived),
        not_evaluated=tuple(not_evaluated),
        rules_evaluated=tuple(sorted((rule.id for rule in active), key=lambda rule_id: order[rule_id])),
    )
