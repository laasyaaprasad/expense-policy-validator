"""Hand-written receipts for edge cases and the 11 rules no gold receipt raises.

Each scenario in fixtures/synthetic_receipts.jsonl gives the expected result for every receipt and
quotes the policy sentence that decides it. Fields left out of a receipt take the defaults in
tests/builders.py; an expected result lists only what is raised, and anything unlisted must be absent.
"""

import json
import re
from pathlib import Path

import pytest

from tests.builders import CONFIG, receipt
from validator.engine import evaluate
from validator.policies import load_policies

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_receipts.jsonl"
SCENARIOS = [json.loads(row) for row in FIXTURES.read_text(encoding="utf-8").splitlines() if row.strip()]


def plain(text: str) -> str:
    return " ".join(re.sub(r"[*`]", "", text).split())


def build(spec: dict):
    fields = {key: value for key, value in spec.items() if key not in ("receipt_id", "lines")}
    return receipt(spec["receipt_id"], *spec["lines"], **fields)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["case"] for s in SCENARIOS])
def test_scenario(scenario):
    decisions = {d.receipt_id: d for d in evaluate([build(spec) for spec in scenario["receipts"]], CONFIG)}
    assert decisions.keys() == scenario["expected"].keys()
    for receipt_id, expected in scenario["expected"].items():
        d = decisions[receipt_id]
        got = {f.rule_id: [line_id.rpartition("-")[2] for line_id in f.line_ids] for f in d.exceptions}
        assert got == expected.get("exceptions", {}), receipt_id
        assert {s.rule_id for s in d.review_signals} == set(expected.get("signals", [])), receipt_id
        assert {w.rule_id for w in d.waived} == set(expected.get("waived", [])), receipt_id
        assert {n.rule_id for n in d.not_evaluated} == set(expected.get("not_evaluated", [])), receipt_id
        assert d.disposition == expected["disposition"], receipt_id


def test_every_scenario_quotes_the_policy():
    corpus = " ".join(plain(rule.text) for rule in load_policies().values())
    misquoted = [s["case"] for s in SCENARIOS if plain(s["policy"]) not in corpus]
    assert misquoted == []


def test_every_tier_2_rule_fires_somewhere():
    tier_2 = {rule.id for rule in CONFIG.rules if rule.tier == 2}
    fired = {
        rule_id
        for s in SCENARIOS
        for expected in s["expected"].values()
        for rule_id in expected.get("exceptions", {})
    }
    assert tier_2 - fired == set()
