"""Deterministic cleanup, a gloss for every non-English line, and a signed-off hand review.

The translation tests replay the committed cache offline: no key, and any cache miss fails.
"""

import csv

import pytest

from validator.data import load_receipts
from validator.llm import LLM
from validator.normaliser import normalise
from validator.translate import REVIEW_PATH, translate_all, translate_receipt


@pytest.mark.parametrize("raw, clean", [
    ("ＵＳＢ－Ｃ　ケーブル　２ｍ", "USB-C ケーブル 2m"),  # full-width Latin, digits, dash and space
    ("iPad Air 11インチ 128GB", "iPad Air 11インチ 128GB"),  # already normal: unchanged
    ("ﾀﾌﾞﾚｯﾄｹｰｽ", "タブレットケース"),  # half-width katakana to full-width
    ("  Salade   nicoise\t\n", "Salade nicoise"),  # whitespace collapsed and trimmed
    ("Tide Pods laundry detergent 42ct", "Tide Pods laundry detergent 42ct"),  # counts are evidence, kept
    ("Weisswein 0,2l", "Weisswein 0,2l"),
])
def test_normalise(raw, clean):
    assert normalise(raw) == clean
    assert normalise(clean) == clean  # idempotent


@pytest.fixture(scope="module")
def receipts():
    return load_receipts()


@pytest.fixture(scope="module")
def glosses(receipts):
    return translate_all(receipts, LLM.offline())


def test_every_non_english_line_has_a_gloss(receipts, glosses):
    foreign = {line.line_id for r in receipts for line in r.line_items if line.language != "en"}
    assert len(foreign) == 11
    assert set(glosses) == foreign
    assert all(gloss.strip() for gloss in glosses.values())


def test_english_receipts_make_no_call(receipts):
    english_only = [r for r in receipts if all(line.language == "en" for line in r.line_items)]
    assert english_only
    for receipt in english_only:
        assert translate_receipt(receipt, LLM.offline()) == {}  # offline: a call would raise


def read_review() -> list[dict]:
    with REVIEW_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_review_sheet_matches_the_glosses(receipts, glosses):
    rows = read_review()
    originals = {line.line_id: line.description_raw for r in receipts for line in r.line_items}
    assert {row["line_id"]: row["english"] for row in rows} == glosses
    assert all(row["original"] == originals[row["line_id"]] for row in rows)


def test_every_gloss_was_checked_by_hand():
    # The one stage with no answer key: a person read each gloss against the original and marked it.
    unchecked = [row["line_id"] for row in read_review() if row["ok"].strip().lower() != "yes"]
    assert unchecked == [], "fill in the ok column of review/translations.csv"
