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
    assert [t.value for t in at.title] == ["Expense Policy Validator"]  # it opens on the problem
    assert at.header[0].value.startswith("Decide flag, review or pass for each of 32 receipts")
    assert [s.value for s in at.subheader][:7] == ["Names that mislead", "Six languages", "Regional overrides",
                                                   "Carve-outs", "Across receipts", "False alarms", "Approach"]
    assert [m.value.split(": ", 1)[1].rstrip("]") for m in at.markdown if "-badge[" in m.value] == [
        "Rejected", "Baseline · 20 / 28", "Selected · 28 / 28"]  # the three designs considered
    assert not at.sidebar.radio  # the results choice only shows where it matters
    at.switch_page("viewer/receipts.py").run()
    assert not at.exception
    assert at.sidebar.radio[0].options == list(SOURCES)


@pytest.mark.parametrize("name, says", [("viewer/overview.py", "<b>All four work the same way:"),
                                        ("viewer/summary.py", "**The keyword baseline**"),
                                        ("viewer/architecture.py", "Coral steps call the model"),
                                        ("viewer/aws.py", "**Cost at 50,000 receipts a day:**")])
def test_the_overview_architecture_and_aws_pages_run(name, says):
    at = page(name)
    assert not at.exception
    assert any(says in m.value for m in [*at.markdown, *at.caption])


def test_the_architecture_diagram_is_drawn_from_its_svg_source():
    at = page("viewer/architecture_diagram.py")
    assert not at.exception
    drawn = next(m.value for m in at.markdown if "<svg" in m.value)
    assert drawn.count("<svg") == 1 and "Seen it before?" in drawn and "Reviewed lines feed the lookup" in drawn
    assert "{display:block" not in drawn.replace(".arch svg{display:block", "")  # every style rule is scoped


def test_the_summary_and_aws_pages_show_their_numbers():
    summary = page("viewer/summary.py")
    table = next(m.value for m in summary.markdown if "| On the 28 scored receipts |" in m.value)
    assert "| Receipts exactly right | **28 / 28** | 20 / 28 |" in table
    assert "| False alarms (should pass) | **0 / 9** | 2 / 9 |" in table
    tiles = {m.label: m.value for m in summary.metric}
    assert tiles["Cost of a run on the 28 scored receipts"] == "$0.0050"
    assert tiles["Total spend on this project"] == "$0.53"
    assert any("**The four timing receipts: all four match.**" in m.value and "**Left out: TIME-04" in m.value
               for m in summary.markdown)
    aws = page("viewer/aws.py")
    assert {m.label: m.value for m in aws.metric}["Per month"] == "about $360"
    assert any("| Model (OpenRouter, Fireworks) |" in m.value for m in aws.markdown)  # the cost table, from the doc
    assert all(m.value.replace(r"\$", "").count("$") < 2 for m in aws.markdown)  # no dollar pair renders as a formula


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


def tables(at) -> list:
    return [t.data if hasattr(t, "data") else t for t in (d.value for d in at.dataframe)]


def table_with(at, *columns):
    return next(t for t in tables(at) if set(columns) <= set(t.columns))


def test_the_detail_page_shows_each_step_of_the_trace_in_the_diagrams_order():
    at = page("viewer/detail.py", source="System", selected="RCP-020")
    assert not at.exception
    assert any("Trace" in t.label for t in at.tabs)
    titles = [m.value.split("**")[1] for m in at.markdown if m.value.startswith("**") and " · " in m.value.split("**")[1]]
    assert [t.split(" · ")[1].split(":")[0] for t in titles] == [
        "Normalise", "Lookup", "Translate", "Classify", "Retrieve", "Engine", "Report", "Routing"]
    engine = table_with(at, "rule", "result")
    steps = dict(zip(engine["rule"], engine["result"]))
    assert steps["TIME-03"] == "raised" and steps["MEAL-01"] == "replaced"
    assert any("7 · Report" in m.value and "Disposition **flag**" in m.value for m in at.markdown)
    # The lookup decides: on RCP-020 it decided none of the lines (not "shadow mode").
    assert any("2 · Lookup" in m.value and "It decided 0 of" in m.value for m in at.markdown)
    assert not any("shadow mode" in m.value for m in at.markdown)

    at = page("viewer/detail.py", source="System", selected="RCP-021")
    assert any("2 · Lookup" in m.value and "It decided 1 of" in m.value for m in at.markdown)
    assert "a second opinion on the lookup" in set(table_with(at, "model's answer", "used as")["used as"])

    at = page("viewer/detail.py", source="System", selected="RCP-016")
    assert any("ALC-01" in m.value and "Regional rules replace" in m.value for m in at.markdown)


def test_the_trace_shows_the_code_checks_and_where_each_line_goes():
    at = page("viewer/detail.py", source="System", selected="RCP-011")
    items = table_with(at, "qty", "amount ($)")
    assert {"unit price (USD)", "amount (USD)"} <= set(items.columns) and items["amount ($)"].sum() == 79.0  # the receipt's total
    retrieved = table_with(at, "rule", "why it was retrieved", "what the rule says")
    says = dict(zip(retrieved["rule"], retrieved["what the rule says"]))
    assert says["ALC-03"].startswith("Alcohol purchased from a package store") and all(says.values())
    checks = dict(zip(*(lambda t: (t["line"], t["code check"]))(table_with(at, "code check"))))
    assert checks["RCP-011-L1"].startswith("straight to the model: the merchant is a package store")
    routing = table_with(at, "goes to", "why")
    goes = dict(zip(routing["line"], zip(routing["goes to"], routing["why"])))
    assert goes["RCP-011-L1"] == ("review queue", "cited by a flag or review (ALC-03)")
    assert goes["RCP-011-L2"][0] == "no review needed"
    # MCC-01 cites no line: the routing step names it with the receipt, not in the line table.
    assert any("The receipt** is **flagged" in m.value and "MCC-01 (high) applies to the whole receipt" in m.value for m in at.markdown)

    at = page("viewer/detail.py", source="System", selected="RCP-015")
    assert any("MEAL-01 (medium) applies to the whole receipt" in m.value and "all 2 exception(s)" in m.value for m in at.markdown)

    at = page("viewer/detail.py", source="System", selected="RCP-023")  # not itemized: no model call at all
    assert any("4 · Classify" in m.value and "isn't itemized" in m.value for m in at.markdown)


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


def test_the_list_says_what_each_receipt_tests_and_searches_items_and_notes():
    at = page("viewer/receipts.py", source="System")
    table = tables(at)[0]
    assert table.set_index("receipt").loc["RCP-004", "what it tests (answer key)"].startswith("Keyword trap")
    at.text_input[0].input("cooking wine").run()
    assert list(tables(at)[0]["receipt"]) == ["RCP-014"]  # found by its line text
    at.text_input[0].input("carve-out").run()
    assert "RCP-027" in set(tables(at)[0]["receipt"])  # found by the answer key's note
    at.text_input[0].input("").run()
    at.multiselect[0].set_value(["review"]).run()
    assert set(tables(at)[0]["ours"]) == {"review"}


def test_the_detail_page_leads_with_the_verdict_and_the_exceptions():
    at = page("viewer/detail.py", source="System", selected="RCP-015")
    assert not at.exception
    card = next(m.value for m in at.markdown if m.value.startswith("#### "))
    assert card.startswith("#### Flagged · 2 exception(s)")
    assert any("**MEAL-01** (medium) · whole receipt" in m.value and "**ALC-02** (medium) · L2" in m.value for m in at.markdown)
    assert all(m.value.replace(r"\$", "").count("$") < 2 for m in at.markdown)  # no unescaped dollar pair renders as a formula
    headings = [h.value for h in at.subheader]
    assert headings.index("Exceptions raised") < headings.index("Line items")
    assert [b.label for b in at.button if "Rulebook" in b.label] == ["Open MEAL-01 in the Rulebook", "Open ALC-02 in the Rulebook"]
    items = table_with(at, "cited by", "category")
    assert dict(zip(items["line"], items["cited by"])) == {"RCP-015-L1": "", "RCP-015-L2": "ALC-02", "RCP-015-L3": ""}


def test_the_trace_shows_exactly_what_the_model_was_sent_and_returned():
    at = page("viewer/detail.py", source="System", selected="RCP-011")
    labels = [e.label for e in at.expander]
    assert any("translation call" in label for label in labels) and any("classification call" in label for label in labels)
    sent = next(c.value for c in at.code if '"merchant"' in c.value)  # the classification call's payload
    returned = next(c.value for c in at.code if '"category"' in c.value and '"reason"' in c.value)
    assert '"merchant": "Cellar Door Fine Wines"' in sent and "79" not in sent  # the payload carries no amounts
    assert '"category": "alcohol"' in returned
    assert any("served by **Fireworks**" in m.value and "temperature 0.0" in m.value for m in at.markdown)


@pytest.mark.parametrize("source", list(SOURCES))
def test_the_rulebook_shows_every_rule(source):
    at = page("viewer/rules.py", source=source)
    assert not at.exception
    assert len(table_with(at, "rule", "title", "what the rule says")) == 40


def test_the_rulebook_explains_a_rule_from_policy_to_verdict():
    at = page("viewer/rules.py", source="System", rule="ALC-02")
    text = "\n".join(m.value for m in at.markdown)
    assert "**ALC-02 — Alcohol per-attendee cap.**" in text  # the policy's own paragraph
    assert "On its own it gives **flag** (medium severity)" in text and "**Where it runs:**" in text
    assert "cites ALC-01, ALC-03" in text and "overridden by REG-01" in text
    assert "Caps are inclusive" in text  # how we read it, from NOTES.md
    assert {m.label: m.value for m in at.metric}["Recall"] == "100%"
    assert "RCP-015" in text and r"\$55.00 each" in text  # a real finding
    assert any(b.label == "Open RCP-015" for b in at.button)
    scenarios = table_with(page("viewer/rules.py", source="System", rule="MEAL-01"), "scenario", "must give")
    assert dict(zip(scenarios["scenario"], scenarios["must give"]))["One cent over the domestic cap"] == "flag: MEAL-01"


def test_the_rulebook_lists_every_field_of_the_rule_with_its_name():
    at = page("viewer/rules.py", source="System", rule="MEAL-01")
    assert at.expander[1].label == "MEAL-01 in `rules.yaml`: every field"  # right below the rule picker
    table = next(m.value for m in at.markdown if m.value.startswith("| Field | YAML key | Value |"))
    for row in ("| **ID** | `id` | MEAL-01 |", "| **Document** | `doc` | `meals-and-entertainment-policy.md` |",
                "| **Kind** | `kind` | exception |", "| **Severity** | `severity` | medium |",
                "| **Numbers** | `params` | domestic_cap_usd: 75 · international_cap_usd: 100 |"):
        assert row in table
    assert "`requires_reference`" not in table  # only the fields the rule has
    assert "| **Waived by** | `requires_reference` |" in next(
        m.value for m in page("viewer/rules.py", source="System", rule="ENT-01").markdown if m.value.startswith("| Field |"))


def test_the_rulebook_shows_a_rules_text_check_receipts_and_tests():
    at = page("viewer/rules.py", source="System", rule="ALC-03")
    assert not at.exception
    assert "def alc_03" in at.code[0].value and "def _broken" in at.code[0].value
    where = table_with(at, "receipt", "raised by us", "in the correct answer")
    assert set(where["receipt"]) >= {"RCP-011", "RCP-014"} and set(where["agree"]) == {"✓"}
    assert any("tests/test_" in m.value for m in at.markdown)
    for rule_id in ("EXC-01", "MCC-03", "REG-02", "TIME-03"):  # mechanics, overrides and takeovers render too
        assert not page("viewer/rules.py", source="System", rule=rule_id).exception


@pytest.mark.parametrize("source", list(SOURCES))
def test_each_receipt_ends_with_its_final_output(source):
    at = page("viewer/detail.py", source=source, selected="RCP-015")
    assert any(e.label == "Final output for this receipt" for e in at.expander)
    record = {"System": "flag", "Keyword baseline": None}[source]
    if record:
        assert any(m.value == "**Disposition:** flag" for m in at.markdown)
        exceptions = table_with(at, "rule", "severity", "policy document", "explanation")
        assert dict(zip(exceptions["rule"], exceptions["lines"])) == {"MEAL-01": "whole receipt", "ALC-02": "RCP-015-L2"}
        assert len(table_with(at, "line", "text", "category", "restricted", "confidence")) == 3
