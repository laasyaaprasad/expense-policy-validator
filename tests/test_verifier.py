"""The verifier experiment's logic (experiments/verifier), offline: no key, no calls."""

import json
from datetime import datetime
from decimal import Decimal

import pytest

from experiments.verifier.__main__ import apply
from experiments.verifier.decisions import Decisions, DecisionError, probability
from experiments.verifier.questions import QUESTION_ID, input_text, predicate
from validator.data import LineItem, ReceiptInput
from validator.models import ClassifiedItem, ClassifiedReceipt


def _receipt() -> ReceiptInput:
    line = LineItem(line_id="R-L1", description_raw="Rum  cake", language="en", quantity=Decimal(1),
                    unit_price=Decimal(9), amount=Decimal(9), amount_usd=Decimal(9))
    return ReceiptInput(receipt_id="R", employee_id="E", employee_country="US", entity="GLOBAL",
                        submitted_expense_type="Meals - Individual", merchant_name="Bakery", mcc="5812",
                        mcc_description="Eating places and restaurants", transaction_datetime=datetime(2026, 1, 1, 12),
                        currency="USD", fx_rate_to_usd=Decimal(1), total_amount=Decimal(9), total_amount_usd=Decimal(9),
                        attendee_count=None, trip_id=None, trip_nights=None, pre_approval_ref=None, itemized=True,
                        line_items=(line,))


def test_the_alcohol_question_carries_both_sides_and_the_taxonomy_s_near_misses():
    q = predicate("alcohol")
    assert q["type"] == "predicate" and q["name"] == QUESTION_ID
    assert "beer" in q["instructions"] and "dishes cooked with alcohol" in q["instructions"]


def test_the_input_holds_what_the_classifier_sees_and_no_amounts():
    text = input_text(_receipt(), "Rum  cake", None)
    assert text == ("Receipt line: Rum cake\nMerchant: Bakery\nMerchant category: Eating places and restaurants\n"
                    "Declared expense type: Meals - Individual")


def _answer(p, model="baseten/mammoth-1"):
    return {"model": model, "answers": [{"type": "predicate", "name": QUESTION_ID, "probability": p}],
            "usage": {"input_tokens": 115}, "provider_metadata": {"gateway": {"cost": "0.0000115", "routing": {"finalProvider": "baseten"}}}}


def test_answers_are_cached_and_logged_without_the_key(tmp_path):
    calls = []

    def post(body):
        calls.append(body)
        return _answer(0.2)

    make = lambda: Decisions(cache_dir=tmp_path / "cache", ledger_path=tmp_path / "spend.jsonl", post=post)
    first = make().ask("Receipt line: x", [predicate("alcohol")], purpose="test")
    again = make().ask("Receipt line: x", [predicate("alcohol")], purpose="test")
    assert len(calls) == 1 and probability(first, QUESTION_ID) == probability(again, QUESTION_ID) == 0.2
    assert calls[0]["providerOptions"] == {"gateway": {"models": ["baseten/mammoth-1"]}}
    entry = json.loads((tmp_path / "spend.jsonl").read_text())
    assert entry["usd"] == 0.0000115 and entry["provider"] == "baseten"


def test_an_answer_from_another_model_is_refused(tmp_path):
    client = Decisions(cache_dir=tmp_path, ledger_path=tmp_path / "s.jsonl", post=lambda body: _answer(0.5, model="other/model"))
    with pytest.raises(DecisionError):
        client.ask("x", [predicate("alcohol")], purpose="test")


def test_a_cache_miss_without_a_key_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr("experiments.verifier.decisions.load_dotenv", lambda *a, **k: None)
    with pytest.raises(DecisionError):
        Decisions(cache_dir=tmp_path, ledger_path=tmp_path / "s.jsonl").ask("x", [], purpose="test")


def test_a_refusal_is_none_and_a_wrong_type_is_an_error():
    assert probability({"answers": [{"type": "refusal", "name": QUESTION_ID}]}, QUESTION_ID) is None
    with pytest.raises(DecisionError):
        probability({"answers": [{"type": "choice", "name": QUESTION_ID, "choice": "a"}]}, QUESTION_ID)


def _classified(category="alcohol", confidence=0.95):
    item = ClassifiedItem(line_id="R-L1", category=category, restricted=True, confidence=confidence)
    return [ClassifiedReceipt(receipt=_receipt(), items={"R-L1": item})]


def test_doubt_lowers_confidence_and_never_changes_the_category():
    (c,) = apply(_classified(), {"R-L1": 0.3}, {}, "doubt")
    assert c.items["R-L1"].category == "alcohol" and c.items["R-L1"].confidence == 0.3


def test_veto_takes_the_best_unrestricted_alternative_only_below_the_bar():
    alts = {"R-L1": [{"category": "gift", "p": 0.03}, {"category": "food_beverage", "p": 0.02}]}
    (vetoed,) = apply(_classified(), {"R-L1": 0.05}, alts, "veto")
    assert vetoed.items["R-L1"].category == "food_beverage" and not vetoed.items["R-L1"].restricted
    (kept,) = apply(_classified(), {"R-L1": 0.2}, alts, "veto")
    assert kept.items["R-L1"].category == "alcohol"
