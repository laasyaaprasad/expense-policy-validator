"""The CORD pilot: the converter on hand-worked cases, and the committed files agree with each other."""

import json
from decimal import Decimal

import pytest

from evaluation.cord import LABELS_PATH, OUT_DIR, RECEIPTS_PATH, build, load_labels
from evaluation.cord_convert import convert, parse_idr, parse_quantity, text
from validator.data import load_receipts
from validator.taxonomy import load_taxonomy


@pytest.mark.parametrize("raw, want", [
    ("16,500", 16500), ("23.000", 23000), ("Rp 29,090", 29090), ("18 000", 18000), ("70000.00", 70000),
    ("-Rp 34,363", -34363), ("Rp. 0", 0), ("@11000", 11000), ("9.091", 9091), (None, None), ("-", None),
])
def test_rupiah_prices(raw, want):
    assert parse_idr(raw) == (None if want is None else Decimal(want))


@pytest.mark.parametrize("raw, want", [("1", 1), ("1X", 1), ("1.00", 1), ("2 X", 2), ("1Prs", 1), ("*2", 2), ("118", 118), (None, 1), ("0", 1)])
def test_quantities(raw, want):
    assert parse_quantity(raw) == Decimal(want)


def test_a_name_split_into_words_is_joined():
    assert text(["-", "Gyro Platter Regular"]) == "- Gyro Platter Regular"


def test_items_then_their_sub_items_become_lines():
    truth = {"gt_parse": {"menu": {"nm": "Milk tea", "cnt": "1X", "price": "17,000",
                                   "sub": [{"nm": "Less Ice"}, {"price": "4,000"}]}},
             "meta": {"split": "valid", "image_id": 7}}
    receipt = convert({"ground_truth": json.dumps(truth)}, 7)
    assert receipt["receipt_id"] == "CORD-V007"
    assert [(i["line_id"], i["description_raw"], i["amount"]) for i in receipt["line_items"]] == [
        ("CORD-V007-L1", "Milk tea", 17000), ("CORD-V007-L2", "Less Ice", 0)]
    assert receipt["source"]["dropped_nameless_items"] == 1
    assert receipt["source"]["total_from_lines"] and receipt["total_amount"] == 17000
    assert receipt["total_amount_usd"] == Decimal("1.06")  # 17,000 / 16,000


def test_every_cord_line_has_one_label_with_the_taxonomy_flag():
    lines = [line.line_id for r in load_receipts(RECEIPTS_PATH) for line in r.line_items]
    labels, taxonomy = load_labels(), load_taxonomy()
    assert sorted(lines) == sorted(labels) and len(lines) == len(set(lines))
    assert all(row["restricted"] == taxonomy[row["category"]] for row in labels.values())
    assert LABELS_PATH.read_text(encoding="utf-8").count("\n") == len(lines)


def test_committed_cord_metrics_are_current():
    committed = json.loads((OUT_DIR / "metrics.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(build(), ensure_ascii=False)) == committed
