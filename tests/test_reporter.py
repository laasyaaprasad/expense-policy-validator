"""M6 gate: every result has the brief's shape, quotes its policy verbatim, and explains its numbers.

Checked on the oracle results for the real receipts and on every synthetic scenario, so each
explanation template runs at least once.
"""

import json
import re

import pytest

from evaluation.oracle import oracle_results
from tests.builders import CONFIG, line, receipt
from tests.test_synthetic_receipts import SCENARIOS, build
from validator.data import POLICY_DIR
from validator.engine import evaluate
from validator.reporter import EXPLAIN, SCHEMA_PATH, ReceiptResult, eur, report, schema, to_json_line, usd

WHOLE_RECEIPT = {rule.id for rule in CONFIG.rules if rule.scope == "receipt"}
# The brief's output shape: these names must be present, spelled exactly so.
BRIEF_FIELDS = {
    None: {"receipt_id", "disposition", "line_items", "exceptions", "review_signals"},
    "line_items": {"line_id", "description_raw", "category", "restricted", "confidence"},
    "exceptions": {"rule_id", "policy_doc", "severity", "line_ids", "evidence", "explanation"},
    "review_signals": {"rule_id", "reason"},
}
# Rule → the computed amount and the cap its explanation must quote, each with its currency format.
THRESHOLD_FACTS = {
    "MEAL-01": (("per_person_day_usd", usd), ("cap_usd", usd)),
    "REG-02": (("per_person_day_usd", usd), ("cap_usd", usd)),
    "REG-03": (("per_person_day_usd", usd), ("cap_eur", eur), ("cap_usd", usd)),
    "MEAL-02": (("total_usd", usd), ("threshold_usd", usd)),
    "ALC-02": (("per_attendee_usd", usd), ("cap_usd", usd)),
    "DOC-01": (("total_usd", usd), ("threshold_usd", usd)),
    "REG-04": (("total_eur", eur), ("threshold_eur", eur)),
}


def plain(text: str) -> str:
    return " ".join(re.sub(r"[*`]", "", text).split())


def _synthetic_results():
    results = []
    extra = [
        [receipt("EXC3", line("food_beverage", "20.00"), line("alcohol", "9.00", confidence=0.42))],
    ]
    for receipts in [[build(spec) for spec in s["receipts"]] for s in SCENARIOS] + extra:
        classified = {c.receipt.receipt_id: c for c in receipts}
        results += [report(classified[d.receipt_id], d, CONFIG) for d in evaluate(receipts, CONFIG)]
    return results


@pytest.fixture(scope="module")
def results() -> list[ReceiptResult]:
    return oracle_results() + _synthetic_results()


def test_schema_file_is_current():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema()


def test_every_result_round_trips_through_the_schema(results):
    # What is written can be read back by the schema model and writes out identically.
    for result in results:
        written = to_json_line(result)
        assert to_json_line(ReceiptResult.model_validate_json(written)) == written


def test_brief_field_names_are_present(results):
    for result in results:
        record = json.loads(to_json_line(result))
        assert BRIEF_FIELDS[None] <= record.keys()
        for part in ("line_items", "exceptions", "review_signals"):
            for entry in record[part]:
                assert BRIEF_FIELDS[part] <= entry.keys(), (result.receipt_id, part)


def test_the_brief_example_validates():
    example = {
        "receipt_id": "RCP-014", "disposition": "flag",
        "line_items": [{"line_id": "RCP-014-L4", "description_raw": "Cooking wine - Marsala 375ml",
                        "category": "alcohol", "restricted": True, "confidence": 0.82}],
        "exceptions": [{"rule_id": "ALC-03", "policy_doc": "meals-and-entertainment-policy.md", "severity": "high",
                        "line_ids": ["RCP-014-L4"],
                        "evidence": "Alcohol purchased from a package store, liquor store, or any other merchant that is not a restaurant, bar or hotel is never reimbursable",
                        "explanation": "Marsala cooking wine bought at a supermarket is retail alcohol."}],
        "review_signals": [{"rule_id": "MCC-02", "reason": "MCC 5411 is medium risk; context only, not an exception.", "policy_doc": "merchant-category-risk.md"}],
    }
    ReceiptResult.model_validate(example)


def test_evidence_is_verbatim_from_its_document(results):
    documents = {path.name: plain(path.read_text(encoding="utf-8")) for path in POLICY_DIR.glob("*.md")}
    for result in results:
        for exception in result.exceptions:
            assert exception.policy_doc == CONFIG.rule(exception.rule_id).doc
            assert plain(exception.evidence) in documents[exception.policy_doc], (result.receipt_id, exception.rule_id)


def test_cited_lines_belong_to_the_receipt(results):
    for result in results:
        own = {item.line_id for item in result.line_items}
        for entry in [*result.exceptions, *result.review_signals, *result.waived]:
            assert set(entry.line_ids) <= own, (result.receipt_id, entry.rule_id)


def test_whole_receipt_rules_cite_no_lines_and_item_rules_no_fees(results):
    for result in results:
        fees = {item.line_id for item in result.line_items if item.category == "service_fee"}
        for exception in result.exceptions:
            if exception.rule_id in WHOLE_RECEIPT:
                assert exception.line_ids == [], (result.receipt_id, exception.rule_id)
            else:
                assert exception.line_ids and not set(exception.line_ids) & fees, (result.receipt_id, exception.rule_id)


def test_each_rule_appears_once_and_mcc_02_always_signals(results):
    for result in results:
        rule_ids = [entry.rule_id for entry in [*result.exceptions, *result.review_signals]]
        assert len(rule_ids) == len(set(rule_ids)), result.receipt_id
    medium = [r for r in oracle_results() if any(s.rule_id == "MCC-02" for s in r.review_signals)]
    assert len(medium) >= 1 and any(r.exceptions for r in medium)  # alongside exceptions too


def test_threshold_explanations_quote_the_amount_and_the_cap(results):
    for result in results:
        for exception in result.exceptions:
            if exception.rule_id not in THRESHOLD_FACTS:
                continue
            for key, money in THRESHOLD_FACTS[exception.rule_id]:
                assert money(exception.facts[key]) in exception.explanation, (result.receipt_id, key, exception.explanation)


def test_unit_price_and_gift_explanations_quote_their_numbers(results):
    for result in results:
        for exception in result.exceptions:
            if exception.rule_id == "TECH-02":
                assert all(usd(price) in exception.explanation for price in exception.facts["unit_price_usd"].values())
                assert usd(exception.facts["cap_usd"]) in exception.explanation
            if exception.rule_id == "GIFT-01":
                assert all(usd(group["total_usd"]) in exception.explanation for group in exception.facts["over_cap"])


def test_every_explanation_is_filled(results):
    seen = set()
    for result in results:
        texts = [e.explanation for e in result.exceptions] + [s.reason for s in result.review_signals] + [w.explanation for w in result.waived]
        for text in texts:
            assert text and "{" not in text and "None" not in text, (result.receipt_id, text)
        seen |= {e.rule_id for e in result.exceptions}
    # Every exception template ran on at least one receipt.
    assert set(EXPLAIN) - seen == set()
