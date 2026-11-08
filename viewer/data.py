"""What the viewer shows, read from committed outputs. The viewer makes no model calls.

The correct answers come from the answer key through evaluation/gold.py, like every other
evaluation; they are shown beside the results, never used to produce them.
"""

import json

import streamlit as st

from evaluation.evaluate import BASELINE_PATH, METRICS_PATH, load_results, receipt_matches
from evaluation.gold import GoldReceipt, in_scope, load_gold
from validator.data import ROOT, ReceiptInput, load_receipts
from validator.pipeline import OUTPUT_DIR

SOURCES = {"System": OUTPUT_DIR / "results.jsonl", "Keyword baseline": BASELINE_PATH}
DEFAULT_SOURCE = "System"
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
def traces() -> dict[str, dict]:
    """The system's per-receipt trace: what each stage did (make run writes it)."""
    with (OUTPUT_DIR / "traces.jsonl").open(encoding="utf-8") as f:
        return {row["receipt_id"]: row for row in map(json.loads, f)}


@st.cache_data
def retrieval_reasons() -> dict[str, dict[str, str]]:
    """Rule ID → why the retriever chose it, per receipt."""
    with (OUTPUT_DIR / "retrieval.jsonl").open(encoding="utf-8") as f:
        return {row["receipt_id"]: row["reasons"] for row in map(json.loads, f)}


@st.cache_data
def metrics() -> dict:
    return json.loads(METRICS_PATH.read_text(encoding="utf-8"))


@st.cache_data
def cord_metrics() -> dict:
    """The frozen system on 100 real CORD receipts (`cord/README.md`), as committed."""
    return json.loads((ROOT / "outputs" / "cord" / "metrics.json").read_text(encoding="utf-8"))


@st.cache_data
def evaluation_section(heading: str) -> str:
    """The body of one `## ` section of docs/evaluation.md, the written answers to Part 3."""
    text = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
    body = text.split(f"\n## {heading}\n", 1)[1]
    return body.split("\n## ", 1)[0].strip()


def match_label(receipt_id: str, record: dict) -> str:
    """✓ or ✗ against the correct answer; the out-of-scope timing receipts say so and never count as mismatches."""
    mark = "✓" if receipt_matches(record, gold()[receipt_id]) else "✗"
    return mark if in_scope(receipt_id) else f"{mark} (not scored)"


def lookup_in_shadow() -> bool:
    """Whether the committed run had the semantic lookup in shadow mode (it records answers in `shadow`, decides none)."""
    return any(line.get("shadow") for trace in traces().values() for line in trace["classify"]["lines"])
