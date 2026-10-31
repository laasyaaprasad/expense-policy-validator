"""M1 gate: the data loads, and its counts and cross-references match the brief and the data dictionary."""

import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import pytest

from evaluation.gold import in_scope, load_gold
from validator.data import LineItem, ReceiptInput, load_receipts
from validator.taxonomy import load_taxonomy

CENT = Decimal("0.01")
APAC = {"IN", "JP", "SG"}
EMEA = {"GB", "DE", "FR", "NL", "IE", "PT", "ES"}
# EXC-04: rules about the receipt as a whole cite no line items.
WHOLE_RECEIPT_RULES = {
    "MCC-01", "MEAL-01", "MEAL-02", "DOC-01", "DOC-02", "DOC-03", "DOC-04",
    "REG-02", "REG-03", "REG-04", "TIME-03",
}


@pytest.fixture(scope="module")
def receipts():
    return load_receipts()


@pytest.fixture(scope="module")
def gold():
    return load_gold()


@pytest.fixture(scope="module")
def taxonomy():
    return load_taxonomy()


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def test_counts(receipts):
    assert len(receipts) == 32
    assert sum(len(r.line_items) for r in receipts) == 79
    scored = [r for r in receipts if in_scope(r.receipt_id)]
    assert len(scored) == 28
    assert sum(len(r.line_items) for r in scored) == 65


def test_in_scope_dispositions(gold):
    counts = Counter(g.disposition for g in gold.values() if in_scope(g.receipt_id))
    assert counts == {"pass": 9, "flag": 16, "review": 3}


def test_ids_are_unique_and_cross_references_resolve(receipts, gold):
    receipt_ids = [r.receipt_id for r in receipts]
    assert len(set(receipt_ids)) == len(receipt_ids)
    assert set(gold) == set(receipt_ids)

    line_ids = [i.line_id for r in receipts for i in r.line_items]
    assert len(set(line_ids)) == len(line_ids)

    for r in receipts:
        own = {i.line_id for i in r.line_items}
        assert set(gold[r.receipt_id].items) == own, r.receipt_id
        for exception in gold[r.receipt_id].exceptions:
            assert set(exception.line_ids) <= own, (r.receipt_id, exception.rule_id)


def test_taxonomy_and_gold_categories_agree(taxonomy, gold):
    assert len(taxonomy) == 21
    for g in gold.values():
        for line_id, item in g.items.items():
            assert item.category in taxonomy, line_id
            assert item.restricted == taxonomy[item.category], line_id


def test_line_amounts_are_consistent(receipts):
    for r in receipts:
        for item in r.line_items:
            assert item.quantity * item.unit_price == item.amount, item.line_id
        assert sum(item.amount for item in r.line_items) == r.total_amount, r.receipt_id


def test_usd_amounts_match_rate(receipts):
    for r in receipts:
        for item in r.line_items:
            assert cents(item.amount * r.fx_rate_to_usd) == cents(item.amount_usd), item.line_id
        assert cents(r.total_amount * r.fx_rate_to_usd) == cents(r.total_amount_usd), r.receipt_id


def test_entity_matches_country(receipts):
    for r in receipts:
        country = r.employee_country
        expected = "APAC" if country in APAC else "EMEA" if country in EMEA else "GLOBAL"
        assert r.entity == expected, r.receipt_id


def test_every_reference_is_a_pre_approval(receipts):
    refs = [r.pre_approval_ref for r in receipts if r.pre_approval_ref]
    assert refs
    assert all(re.fullmatch(r"PA-\d{4}-\d+", ref) for ref in refs), refs


def test_value_domains(receipts):
    assert {r.currency for r in receipts} <= {"USD", "EUR", "INR", "JPY"}
    assert {i.language for r in receipts for i in r.line_items} <= {"en", "es", "fr", "de", "pt", "ja"}


def test_input_models_cannot_carry_answers():
    for model in (ReceiptInput, LineItem):
        assert not {"labels", "notes"} & set(model.model_fields), model.__name__


def test_each_rule_at_most_once_per_receipt(gold):
    for g in gold.values():
        exception_ids = [e.rule_id for e in g.exceptions]
        signal_ids = [s.rule_id for s in g.review_signals]
        assert len(set(exception_ids)) == len(exception_ids), g.receipt_id
        assert len(set(signal_ids)) == len(signal_ids), g.receipt_id


def test_line_ids_follow_scope_convention(gold):
    for g in gold.values():
        for exception in g.exceptions:
            where = (g.receipt_id, exception.rule_id)
            if exception.rule_id in WHOLE_RECEIPT_RULES:
                assert exception.line_ids == (), where
            else:
                assert exception.line_ids, where
                assert all(g.items[line].category != "service_fee" for line in exception.line_ids), where


def test_non_itemized_lines_are_other(receipts, gold):
    non_itemized = [r for r in receipts if not r.itemized]
    assert non_itemized
    for r in non_itemized:
        categories = {gold[r.receipt_id].items[i.line_id].category for i in r.line_items}
        assert categories == {"other"}, r.receipt_id
