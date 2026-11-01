"""Loads rules/rules.yaml: the policies as data, validated on load."""

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from validator.data import ROOT

RULES_PATH = ROOT / "rules" / "rules.yaml"


class AppliesTo(BaseModel):
    """When the retriever selects a rule: the receipt's region passes `regions`
    (empty means every region), and any one of the other conditions matches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    regions: tuple[str, ...] = ()
    always: bool = False
    categories: tuple[str, ...] = ()
    item_kinds: tuple[str, ...] = ()
    recipient_types: tuple[str, ...] = ()
    names_recipient: bool = False
    expense_types: tuple[str, ...] = ()
    mcc_risk: tuple[str, ...] = ()


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    doc: str
    kind: Literal["exception", "review_signal", "mechanic"]
    severity: str | dict[str, str] | None = None
    scope: Literal["receipt", "items"] | None = None
    tier: Literal[1, 2] | None = None
    params: dict[str, Decimal] = {}
    applies_to: AppliesTo = AppliesTo()
    overrides: tuple[str, ...] = ()  # rules this one replaces wherever it applies
    line_overrides: tuple[str, ...] = ()  # rules this one replaces for the lines it cites
    replaces: tuple[str, ...] = ()  # rules this one may replace on a receipt where it is raised (TIME-03)
    requires_reference: str | None = None  # the reference kind that satisfies the rule (EXC-02)
    evidence: str

    @model_validator(mode="after")
    def _check_kind(self) -> "RuleSpec":
        if self.kind == "exception":
            ok, need = bool(self.severity and self.scope and self.tier), "a severity, scope and tier"
        elif self.kind == "review_signal":
            ok, need = self.severity is None and bool(self.tier), "a tier and no severity"
        else:
            ok, need = self.severity is None and self.tier is None, "no severity or tier"
        if not ok:
            raise ValueError(f"{self.id}: a {self.kind} needs {need}")
        return self


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regions: dict[str, tuple[str, ...]]
    home_currency: dict[str, str]
    euro_rate_usd: Decimal
    mcc_risk: dict[str, tuple[str, ...]]
    on_premises_mccs: tuple[str, ...]
    meal_expense_types: tuple[str, ...]
    expense_type_covers: dict[str, tuple[str, ...]]
    reference_prefixes: dict[str, str]
    out_of_scope: tuple[str, ...]
    rules: tuple[RuleSpec, ...]

    @model_validator(mode="after")
    def _check_ids(self) -> "RulesConfig":
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate rule IDs")
        if set(ids) & set(self.out_of_scope):
            raise ValueError("a rule is both encoded and out of scope")
        return self

    def rule(self, rule_id: str) -> RuleSpec:
        return next(rule for rule in self.rules if rule.id == rule_id)

    def region_of(self, country: str) -> str:
        return next((name for name, members in self.regions.items() if country in members), "GLOBAL")

    def mcc_tier(self, mcc: str) -> str:
        return next((tier for tier, codes in self.mcc_risk.items() if mcc in codes), "low")


def load_rules(path: Path = RULES_PATH) -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def policy_version(path: Path = RULES_PATH) -> str:
    """Short hash of rules.yaml, recorded on every result for audit."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
