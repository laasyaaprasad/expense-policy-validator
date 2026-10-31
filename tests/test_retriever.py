"""M5 gate: the retriever never drops a rule the engine needs.

Run on the correct categories, so a failure is the retriever's alone: a missing category, MCC or
region in some rule's `applies_to`. Fix the YAML, not the engine.
"""

import pytest

from evaluation.gold import in_scope, load_gold
from evaluation.oracle import load_attributes, oracle_receipts
from tests.builders import CONFIG
from tests.test_synthetic_receipts import SCENARIOS, build
from validator.data import load_receipts
from validator.engine import evaluate
from validator.policies import load_policies
from validator.retriever import retrieve

POLICIES = load_policies()


@pytest.fixture(scope="module")
def gold():
    return load_gold()


@pytest.fixture(scope="module")
def receipts(gold):
    return oracle_receipts(load_receipts(), gold, load_attributes())


def retrieved_for(receipts):
    return {c.receipt.receipt_id: retrieve(c, CONFIG, POLICIES).rule_ids for c in receipts}


def without_audit_trail(decisions):
    """Decisions as the answer key sees them; which rules were evaluated is expected to differ."""
    return [d.model_copy(update={"rules_evaluated": ()}) for d in decisions]


def test_recall_is_100_percent(receipts, gold):
    retrieved = retrieved_for(receipts)
    missed = {}
    for receipt_id, g in gold.items():
        if not in_scope(receipt_id):
            continue
        needed = {finding.rule_id for finding in (*g.exceptions, *g.review_signals)}
        if needed - set(retrieved[receipt_id]):
            missed[receipt_id] = sorted(needed - set(retrieved[receipt_id]))
    assert missed == {}


def test_engine_output_is_unchanged_on_the_real_receipts(receipts):
    narrowed = evaluate(receipts, CONFIG, retrieved=retrieved_for(receipts))
    assert without_audit_trail(narrowed) == without_audit_trail(evaluate(receipts, CONFIG))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["case"] for s in SCENARIOS])
def test_engine_output_is_unchanged_on_the_synthetic_receipts(scenario):
    # These cover the tier-2 rules (item kinds, recipients, regions) that no real receipt exercises.
    receipts = [build(spec) for spec in scenario["receipts"]]
    narrowed = evaluate(receipts, CONFIG, retrieved=retrieved_for(receipts))
    assert without_audit_trail(narrowed) == without_audit_trail(evaluate(receipts, CONFIG))


def test_retrieval_narrows_and_explains_itself(receipts):
    retrievals = [retrieve(c, CONFIG, POLICIES) for c in receipts]
    rules_that_raise = sum(rule.kind != "mechanic" for rule in CONFIG.rules)
    average = sum(len(r.rule_ids) for r in retrievals) / len(retrievals)
    assert average < rules_that_raise / 2, average
    for r in retrievals:
        assert set(r.reasons) == set(r.rule_ids) and all(r.reasons.values()), r.receipt_id


def test_regional_rules_stay_in_their_region(receipts):
    for c in receipts:
        region = CONFIG.region_of(c.receipt.employee_country)
        for rule_id in retrieve(c, CONFIG, POLICIES).rule_ids:
            regions = CONFIG.rule(rule_id).applies_to.regions
            assert not regions or region in regions, (c.receipt.receipt_id, rule_id)
