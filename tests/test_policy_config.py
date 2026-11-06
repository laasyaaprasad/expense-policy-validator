"""Experiment 3's logic (experiments/policy_config), offline: a fake model, no key, no calls."""

import json
from types import SimpleNamespace

import pytest

from evaluation.gold import load_gold
from experiments.policy_config.extract import Extracted, extract, problems, to_spec
from experiments.policy_config.prompt import related_rules, system_prompt, user_message
from experiments.policy_config.score import build, compare, end_to_end, lenient, majority, value
from validator.llm import LLM, Settings
from validator.policies import load_policies
from validator.rules_config import load_rules


@pytest.fixture(scope="module")
def policies():
    return load_policies()


@pytest.fixture(scope="module")
def reference():
    return load_rules()


def test_the_prompts_carry_no_rules_yaml_values(policies, reference):
    system = system_prompt(reference)
    assert not any(rule_id in system for rule_id in policies), "a real rule ID leaked into the instructions"
    for spec in reference.rules:
        payload = json.loads(user_message(policies[spec.id], sorted(spec.params), [], related_rules(spec.id, policies)))
        assert set(payload["rule"]) <= {"id", "title", "document", "section", "text", "parameter_names", "severity_cases"}
        assert payload["rule"]["text"] == policies[spec.id].text  # the policy text, nothing from rules.yaml


def test_retrieval_follows_citations_both_ways(policies):
    related = {r.id for r in related_rules("MEAL-01", policies)}
    assert "REG-02" in related and "REG-03" in related  # they cite MEAL-01
    assert "MEAL-01" in {r.id for r in related_rules("REG-02", policies)}  # REG-02 cites it


def _extracted(spec) -> Extracted:
    return Extracted(kind=spec.kind, severity=spec.severity, scope=spec.scope,
                     params={k: float(v) for k, v in spec.params.items()}, applies_to=spec.applies_to,
                     overrides=list(spec.overrides), line_overrides=list(spec.line_overrides), replaces=list(spec.replaces),
                     requires_reference=spec.requires_reference, evidence=spec.evidence)


def test_a_perfect_extraction_rebuilds_the_reference(policies, reference):
    generated = {s.id: to_spec(policies[s.id], _extracted(s), s.tier) for s in reference.rules}
    scored = compare(generated, reference, policies)
    assert scored["rules_exactly_right"] == {"k": 40, "n": 40} and scored["diffs"] == {}
    assert build(generated, reference) == reference


def test_the_checks_catch_wrong_names_values_and_ids(policies, reference):
    spec = reference.rule("MEAL-01")
    check = problems(policies["MEAL-01"], sorted(spec.params), [], reference, set(policies), spec.tier)
    assert check(_extracted(spec)) == []
    bad = _extracted(spec).model_copy(update={"params": {"cap": 75.0}, "overrides": ["NOPE-99"]})
    found = " ".join(check(bad))
    assert "exactly these names" in found and "don't exist" in found


def test_a_wrong_field_is_listed_as_a_diff(policies, reference):
    generated = {s.id: to_spec(policies[s.id], _extracted(s), s.tier) for s in reference.rules}
    generated["ALC-01"] = generated["ALC-01"].model_copy(update={"severity": "medium"})
    scored = compare(generated, reference, policies)
    assert scored["rules_exactly_right"]["k"] == 39 and set(scored["diffs"]["ALC-01"]) == {"severity"}


def test_majority_vote_and_flags(reference):
    a = reference.rule("ALC-02")
    b = a.model_copy(update={"severity": "high"})
    voted, flagged = majority([a, b, a])
    assert voted.severity == a.severity and flagged == ["severity"]


def test_lenient_turns_unchecked_rules_into_mechanics(reference):
    broken = reference.model_copy(update={"rules": tuple(
        r.model_copy(update={"kind": "exception", "severity": "low", "scope": "receipt", "tier": 2}) if r.id == "EXC-01" else r
        for r in reference.rules)})
    repaired, changed = lenient(broken)
    assert changed == ["EXC-01"] and repaired.rule("EXC-01").kind == "mechanic"


def test_the_hand_config_scores_28_of_28_through_this_scoring(policies, reference):
    gold = load_gold()
    result = end_to_end(reference, policies, gold, [], {})
    assert result["oracle"]["exactly_right"] == {"k": 28, "n": 28}
    assert result["system"]["exactly_right"] == {"k": 28, "n": 28}


class FakeCompletions:
    def __init__(self, reply: str):
        self.reply, self.calls = reply, 0

    def create(self, **params):
        self.calls += 1
        message = SimpleNamespace(content=self.reply, model_dump=lambda: {})
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0})
        return SimpleNamespace(choices=[SimpleNamespace(message=message, logprobs=None, finish_reason="stop")],
                               model="fake", system_fingerprint=None, usage=usage)


def test_extraction_round_trip_through_a_fake_model_uses_its_own_cache_and_ledger(policies, reference, tmp_path):
    spec = reference.rule("REG-02")
    reply = _extracted(spec).model_dump_json()
    fake = FakeCompletions(reply)
    llm = LLM(Settings(provider="openrouter", model="m", base_url="u", api_key="k"),
              client=SimpleNamespace(chat=SimpleNamespace(completions=fake)), cache_dir=tmp_path / "cache",
              ledger_path=tmp_path / "spend.jsonl")
    got = extract(llm, policies["REG-02"], policies, reference, parameter_names=sorted(spec.params), severity_cases=[],
                  tier=spec.tier, retrieval=True, temperature=0)
    assert all(value(got, f) == value(spec, f) for f in ("kind", "severity", "params", "overrides", "applies_to"))
    assert fake.calls == 1 and (tmp_path / "spend.jsonl").exists()
