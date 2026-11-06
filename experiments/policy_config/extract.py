"""Extraction: one model call per rule, validated against the real config schema, retried once.

A reply is accepted only if it builds a valid RuleSpec (validator/rules_config.py), uses only the
allowed values, names only real rule IDs, and fills exactly the parameter names it was given.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from experiments.policy_config.prompt import (PROMPT_VERSION, REFERENCE_KINDS, SEVERITIES, allowed_values,
                                              related_rules, system_prompt, user_message)
from validator.llm import LLM, LLMError, complete_json
from validator.policies import Rule
from validator.rules_config import AppliesTo, RuleSpec, RulesConfig

MAX_TOKENS = 1500


class Extracted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["exception", "review_signal", "mechanic"]
    severity: str | dict[str, str] | None = None
    scope: Literal["items", "receipt"] | None = None
    params: dict[str, float] = {}
    applies_to: AppliesTo = AppliesTo()
    overrides: list[str] = []
    line_overrides: list[str] = []
    replaces: list[str] = []
    requires_reference: str | None = None
    evidence: str


def _number(value: float) -> Decimal:
    number = Decimal(str(value))
    return Decimal(int(number)) if number == number.to_integral_value() else number


def to_spec(rule: Rule, extracted: Extracted, tier: int | None) -> RuleSpec:
    """The RuleSpec the engine reads. `id` and `doc` come from the parser; `tier` is copied from
    rules.yaml (PLAN.md: not in the policy text, and never read by the engine)."""
    if extracted.kind == "mechanic":
        tier = None
    elif tier is None:
        tier = 2  # the reference calls it a mechanic; any tier satisfies the schema
    return RuleSpec(
        id=rule.id, doc=rule.doc, kind=extracted.kind, severity=extracted.severity, scope=extracted.scope, tier=tier,
        params={name: _number(value) for name, value in extracted.params.items()},
        applies_to=extracted.applies_to, overrides=tuple(extracted.overrides), line_overrides=tuple(extracted.line_overrides),
        replaces=tuple(extracted.replaces), requires_reference=extracted.requires_reference, evidence=extracted.evidence)


def problems(rule: Rule, parameter_names: list[str], severity_cases: list[str], shared: RulesConfig,
             known_ids: set[str], tier: int | None):
    values = allowed_values(shared)

    def check(reply: Extracted) -> list[str]:
        found = []
        if set(reply.params) != set(parameter_names):
            found.append(f"params must have exactly these names: {parameter_names}")
        severities = reply.severity.values() if isinstance(reply.severity, dict) else [reply.severity]
        if reply.kind == "exception" and not all(s in SEVERITIES for s in severities):
            found.append(f"severity must be one of {list(SEVERITIES)}")
        if isinstance(reply.severity, dict) and set(reply.severity) != set(severity_cases):
            found.append(f"severity cases must be exactly {severity_cases}")
        if reply.requires_reference is not None and reply.requires_reference not in REFERENCE_KINDS:
            found.append(f"requires_reference must be one of {list(REFERENCE_KINDS)} or null")
        a = reply.applies_to
        for field, allowed in (("categories", values["categories"]), ("item_kinds", values["item_kinds"]),
                               ("recipient_types", values["recipient_types"]), ("expense_types", values["expense_types"]),
                               ("regions", values["regions"]), ("mcc_risk", values["mcc_risk"])):
            if unknown := set(getattr(a, field)) - set(allowed):
                found.append(f"applies_to.{field} has values that are not allowed: {sorted(unknown)}")
        for field in ("overrides", "line_overrides", "replaces"):
            if unknown := set(getattr(reply, field)) - known_ids:
                found.append(f"{field} names rules that don't exist: {sorted(unknown)}")
        if not found:
            try:
                to_spec(rule, reply, tier)
            except ValidationError as error:
                found += [e["msg"] for e in error.errors()]
        return found

    return check


def extract(llm: LLM, rule: Rule, policies: dict[str, Rule], shared: RulesConfig, *, parameter_names: list[str],
            severity_cases: list[str], tier: int | None, retrieval: bool, temperature: float, sample: str = "") -> RuleSpec:
    """One rule's config entry. Raises LLMError if the reply is still invalid after the retry."""
    related = related_rules(rule.id, policies) if retrieval else []
    messages = [{"role": "system", "content": system_prompt(shared)},
                {"role": "user", "content": user_message(rule, parameter_names, severity_cases, related)}]
    version = PROMPT_VERSION + ("-retrieval" if retrieval else "") + (f"-{sample}" if sample else "")
    reply, _ = complete_json(llm, messages, Extracted, purpose="policy_config", prompt_version=version,
                             check=problems(rule, parameter_names, severity_cases, shared, set(policies), tier),
                             temperature=temperature, max_tokens=MAX_TOKENS)
    return to_spec(rule, reply, tier)
