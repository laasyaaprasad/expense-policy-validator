"""Every page of the viewer runs, and the list highlights exactly the mismatches metrics.json counts."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from evaluation.evaluate import METRICS_PATH
from validator.data import ROOT
from viewer.data import SOURCES

METRICS = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
EXACT = {"System": METRICS["exact_match"]["k"], "Keyword baseline": METRICS["baseline"]["exact_match"]["k"]}


def page(name: str, **state) -> AppTest:
    at = AppTest.from_file(str(ROOT / name), default_timeout=60)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def test_the_app_runs_with_its_navigation():
    at = page("app.py")
    assert not at.exception
    assert at.sidebar.radio[0].options == list(SOURCES)


@pytest.mark.parametrize("source", list(SOURCES))
def test_the_list_shows_all_32_and_tints_the_mismatches_metrics_counts(source):
    at = page("viewer/receipts.py", source=source)
    assert not at.exception
    table = at.dataframe[0].value
    assert len(table) == 32
    assert int((table["match"] == "✗").sum()) == 28 - EXACT[source]
    assert int(table["match"].str.endswith("(not scored)").sum()) == 4


def test_the_timing_receipts_show_whether_they_match_without_counting():
    at = page("viewer/receipts.py", source="System")
    table = at.dataframe[0].value
    table = table.data if hasattr(table, "data") else table
    marks = dict(zip(table["receipt"], table["match"]))
    assert {marks[r] for r in ("RCP-020", "RCP-021", "RCP-029", "RCP-030")} == {"✓ (not scored)"}
    assert at.metric[0].value == "28 / 28"


@pytest.mark.parametrize("source", list(SOURCES))
def test_every_receipt_detail_renders(source):
    for n in range(1, 33):
        at = page("viewer/detail.py", source=source, selected=f"RCP-{n:03d}")
        assert not at.exception, f"RCP-{n:03d}"
        assert at.selectbox[0].value == f"RCP-{n:03d}"


def test_the_metrics_page_runs():
    at = page("viewer/metrics.py")
    assert not at.exception
    assert any("Classification" in h.value for h in at.subheader)


def test_the_detail_page_shows_each_stage_of_the_trace():
    at = page("viewer/detail.py", source="System", selected="RCP-020")
    assert not at.exception
    assert any("Trace" in t.label for t in at.tabs)
    engine = at.dataframe[-1].value
    engine = engine.data if hasattr(engine, "data") else engine
    steps = dict(zip(engine["rule"], engine["result"]))
    assert steps["TIME-03"] == "raised" and steps["MEAL-01"] == "replaced"
    assert any("Disposition" in m.value and "flag" in m.value for m in at.markdown)

    at = page("viewer/detail.py", source="System", selected="RCP-016")
    assert any("ALC-01" in m.value and "Regional rules replace" in m.value for m in at.markdown)


def test_the_mismatch_filter_keeps_only_the_mismatches():
    at = page("viewer/receipts.py", source="Keyword baseline")
    at.toggle[0].set_value(True).run()
    assert not at.exception
    table = at.dataframe[0].value
    table = table.data if hasattr(table, "data") else table
    assert len(table) == 28 - EXACT["Keyword baseline"] and set(table["match"]) == {"✗"}


def test_next_and_previous_step_through_the_receipts():
    at = page("viewer/detail.py", source="System", selected="RCP-032")
    next(b for b in at.button if b.label == "Next").click().run()
    assert at.selectbox[0].value == "RCP-001"
    next(b for b in at.button if b.label == "Previous").click().run()
    assert at.selectbox[0].value == "RCP-032"


def test_the_metrics_page_answers_the_failure_and_base_rate_questions():
    at = page("viewer/metrics.py")
    assert any("base-rate" in h.value for h in at.subheader)
    assert any("CORD" in h.value for h in at.subheader)
    tiles = {m.label: m.value for m in at.metric}
    assert tiles["Lines right"] == "255 / 270" and tiles["False alarms"] == "10 / 98" and tiles["Violations caught"] == "2 / 2"
    assert any("TALAM UNGU" in m.value for m in at.markdown)
