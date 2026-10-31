"""What the viewer shows, read from committed outputs. The viewer makes no model calls.

The correct answers come from the answer key through evaluation/gold.py, like every other
evaluation; they are shown beside the results, never used to produce them.
"""

import json

import streamlit as st

from evaluation.evaluate import BASELINE_PATH, METRICS_PATH, load_results, receipt_matches
from evaluation.gold import GoldReceipt, in_scope, load_gold
from validator.data import ReceiptInput, load_receipts
from validator.pipeline import OUTPUT_DIR

SOURCES = {"System (v1)": OUTPUT_DIR / "results.jsonl", "Keyword baseline": BASELINE_PATH}
DEFAULT_SOURCE = "System (v1)"
MISMATCH = "rgba(255, 80, 80, 0.22)"  # row tint that reads on light and dark themes


def current_source() -> str:
    """Chosen in the sidebar of app.py; a page run on its own shows the system."""
    return st.session_state.get("source", DEFAULT_SOURCE)


@st.cache_data
def results(source: str) -> dict[str, dict]:
    return load_results(SOURCES[source])


@st.cache_resource
def gold() -> dict[str, GoldReceipt]:
    return load_gold()


@st.cache_resource
def receipts() -> dict[str, ReceiptInput]:
    return {r.receipt_id: r for r in load_receipts()}


@st.cache_data
def glosses() -> dict[str, str]:
    with (OUTPUT_DIR / "classifications.jsonl").open(encoding="utf-8") as f:
        return {row["line_id"]: row["english"] for row in map(json.loads, f) if row["english"]}


@st.cache_data
def metrics() -> dict:
    return json.loads(METRICS_PATH.read_text(encoding="utf-8"))


def match_label(receipt_id: str, record: dict) -> str:
    if not in_scope(receipt_id):
        return "not scored"
    return "✓" if receipt_matches(record, gold()[receipt_id]) else "✗"
