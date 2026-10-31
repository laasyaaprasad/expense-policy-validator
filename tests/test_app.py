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
    assert int((table["match"] == "not scored").sum()) == 4


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
