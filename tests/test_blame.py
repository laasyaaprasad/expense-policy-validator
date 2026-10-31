"""The blame report names the right stage. The committed run has no mismatches, so each stage is
checked by planting one fault in its classifications and seeing which stage the report blames."""

import pytest

from evaluation.blame import blame, load_classified, load_gloss_review
from evaluation.gold import GoldException, load_gold
from evaluation.oracle import load_attributes, run_oracle
from validator import pipeline
from validator.classifier import TAXONOMY
from validator.retriever import Retrieval

GOLD = load_gold()
ATTRIBUTES = load_attributes()
GLOSS_OK = load_gloss_review()
ORACLE = run_oracle()


@pytest.fixture(scope="module")
def committed():
    return load_classified()


def plant(classified, line_id, **updates):
    """The committed classifications with one line changed; restricted follows the category."""
    if "category" in updates:
        updates["restricted"] = TAXONOMY[updates["category"]]
    rid = line_id.rsplit("-", 1)[0]
    return [c.model_copy(update={"items": {**c.items, line_id: c.items[line_id].model_copy(update=updates)}})
            if c.receipt.receipt_id == rid else c for c in classified]


def stages(classified, gloss_ok=GLOSS_OK, oracle=ORACLE, gold=GOLD, scored=None):
    return {b.receipt_id: b.stage for b in blame(classified, gold, ATTRIBUTES, gloss_ok, oracle, scored)}


def test_the_committed_run_has_nothing_to_blame(committed):
    assert stages(committed) == {}


def test_a_misclassified_item_is_classification(committed):
    # Rum cake read as alcohol: a restricted item on a receipt that should pass.
    assert stages(plant(committed, "RCP-004-L4", category="alcohol")) == {"RCP-004": "classification"}


def test_a_misclassified_item_with_a_bad_gloss_is_translation(committed):
    faulty = plant(committed, "RCP-011-L1", category="food_beverage")
    assert stages(faulty) == {"RCP-011": "classification"}  # the gloss was checked and is fine
    assert stages(faulty, gloss_ok={**GLOSS_OK, "RCP-011-L1": False}) == {"RCP-011": "translation"}


def test_a_wrong_recipient_is_recipient_extraction(committed):
    faulty = plant(plant(committed, "RCP-022-L1", recipient_type="client"), "RCP-022-L2", recipient_type="client")
    assert stages(faulty) == {"RCP-022": "recipient extraction"}


def test_an_uncertain_item_is_the_threshold(committed):
    assert stages(plant(committed, "RCP-012-L1", confidence=0.1)) == {"RCP-012": "confidence threshold"}


def test_a_dropped_rule_is_retrieval(committed, monkeypatch):
    real = pipeline.retrieve

    def drops_tech_01(classified, *args):
        r = real(classified, *args)
        return Retrieval(r.receipt_id, tuple(rule for rule in r.rule_ids if rule != "TECH-01"), r.reasons) \
            if r.receipt_id == "RCP-012" else r

    monkeypatch.setattr(pipeline, "retrieve", drops_tech_01)
    assert stages(committed) == {"RCP-012": "retrieval"}


def test_a_receipt_oracle_mode_also_gets_wrong_is_rule_logic(committed):
    oracle = [d.model_copy(update={"disposition": "flag"}) if d.receipt_id == "RCP-004" else d for d in ORACLE]
    assert stages(plant(committed, "RCP-004-L4", category="alcohol"), oracle=oracle) == {"RCP-004": "rule logic"}


def test_a_misclassification_on_another_receipt_is_cross_receipt(committed):
    # RCP-020 and RCP-021 are one employee's meals on one day. With RCP-021's dishes read as office
    # supplies, the day's meal spend falls under the cap and RCP-020 loses MEAL-01. The pair is out of
    # scope (gold has TIME-03), so RCP-020 is scored here against the engine's own MEAL-01 answer.
    [meal_01] = [f for d in ORACLE if d.receipt_id == "RCP-020" for f in d.exceptions]
    gold = {**GOLD, "RCP-020": GOLD["RCP-020"].model_copy(update={"exceptions": (GoldException(
        rule_id="MEAL-01", policy_doc=meal_01.policy_doc, severity=meal_01.severity, line_ids=(), reason="test"),)})}
    faulty = committed
    for line_id in ("RCP-021-L1", "RCP-021-L2", "RCP-021-L3"):
        faulty = plant(faulty, line_id, category="office_supplies")
    assert stages(committed, gold=gold, scored={"RCP-020"}) == {}
    assert stages(faulty, gold=gold, scored={"RCP-020"}) == {"RCP-020": "classification (cross-receipt)"}


def test_two_faults_on_one_item_are_unknown(committed):
    # Wrong category and too uncertain: no single correction fixes it, so the report says so.
    faulty = plant(committed, "RCP-012-L1", category="computer_accessory", confidence=0.1)
    assert stages(faulty) == {"RCP-012": "unknown"}
