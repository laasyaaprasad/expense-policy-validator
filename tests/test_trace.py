"""The trace: one record per receipt saying how its decision was reached, agreeing with the results
and holding no line text or names. The run log: what made a run and what its calls did."""

import json
import re
from datetime import datetime, timezone

import pytest

from validator.classifier import TAXONOMY, category_alternatives
from validator.data import load_receipts
from validator.llm import CACHE_DIR, LLM, Completion, Token
from validator.pipeline import OUTPUT_DIR, run
from validator.trace import run_record


def read(name: str) -> list[dict]:
    with (OUTPUT_DIR / name).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.fixture(scope="module")
def traces():
    return read("traces.jsonl")


@pytest.fixture(scope="module")
def results():
    return read("results.jsonl")


def test_one_trace_per_result_with_the_same_disposition(traces, results):
    assert [(t["receipt_id"], t["disposition"]) for t in traces] == [(r["receipt_id"], r["disposition"]) for r in results]


def test_every_rule_the_engine_ran_has_a_result_that_matches_the_output(traces, results):
    for t, r in zip(traces, results):
        rules = t["engine"]["rules"]
        found = lambda result: {rule_id for rule_id, step in rules.items() if step["result"] == result}
        assert set(rules) - {"EXC-03"} == set(r["rules_evaluated"])
        assert found("raised") == {e["rule_id"] for e in r["exceptions"]}
        assert found("signal") == {s["rule_id"] for s in r["review_signals"]}
        assert found("waived") == {w["rule_id"] for w in r["waived"]}
        assert found("not_evaluated") == {n["rule_id"] for n in r["not_evaluated"]}


def test_the_retrieved_rules_are_the_retrieval_log(traces):
    assert [t["retrieve"]["rules"] for t in traces] == [r["rule_ids"] for r in read("retrieval.jsonl")]


def test_a_cross_receipt_total_names_the_receipts_behind_it(traces):
    by_id = {t["receipt_id"]: t for t in traces}
    # The lunch and dinner one employee split around the meal cap (RCP-020/021) share one day total.
    assert by_id["RCP-020"]["engine"]["totals"]["meal_day"]["receipts"] == ["RCP-020", "RCP-021"]
    for t in traces:
        if "meal_day" in t["engine"]["totals"]:
            assert t["receipt_id"] in t["engine"]["totals"]["meal_day"]["receipts"]


def test_overrides_are_recorded(traces):
    by_id = {t["receipt_id"]: t for t in traces}
    assert by_id["RCP-022"]["engine"]["lines_taken_over"] == {"GIFT-01": ["RCP-022-L1", "RCP-022-L2"]}  # GIFT-04 first
    assert "MEAL-01" in by_id["RCP-016"]["engine"]["overridden_by_region"]  # an EMEA employee: REG-02/03 instead


def test_the_trace_holds_no_line_text_or_names(traces):
    texts = {line.description_raw for receipt in load_receipts() for line in receipt.line_items}
    names = {row["recipient_name"] for row in read("classifications.jsonl") if row["recipient_name"]}
    strings = set()
    walk = lambda value: ([walk(v) for v in value.values()] if isinstance(value, dict) else
                          [walk(v) for v in value] if isinstance(value, list) else
                          strings.add(value) if isinstance(value, str) else None)
    walk(traces)
    blob = (OUTPUT_DIR / "traces.jsonl").read_text(encoding="utf-8")
    assert names and not strings & texts
    assert not [name for name in names if name in blob]


def test_runner_ups_are_other_categories(traces):
    lines = [line for t in traces for line in t["classify"]["lines"]]
    assert any(line["alternatives"] for line in lines)
    for line in lines:
        for alternative in line["alternatives"]:
            assert alternative["category"] in (None, *TAXONOMY) and alternative["category"] != line["category"]
            assert 0 <= alternative["p"] <= 1


@pytest.mark.parametrize("tokens, start", [
    (['{"category": "', "al", 'cohol"}'], 14),   # the category starts a token
    (['{"category":', ' "al', 'cohol"}'], 14),   # a quote shares the category's first token
])
def test_a_runner_up_is_read_from_the_categorys_first_token(tokens, start):
    lead = tokens[1][: len(tokens[1]) - 2]
    top = ((tokens[1], -0.1), (lead + "food", -2.5), (lead + "g", -4.0), (lead + "alc", -5.0), ("\n", -6.0))
    completion = Completion(content="".join(tokens), model="m", usage={}, cache_key="k", cached=True,
                            tokens=(Token(tokens[0], 0.0), Token(tokens[1], -0.1, top), Token(tokens[2], 0.0)))
    found = category_alternatives(completion, start, "alcohol")
    # "food" is food_beverage; "g" starts several categories; "alc" is alcohol again, so it is skipped.
    assert [(a["category"], a["token"]) for a in found] == [("food_beverage", "food"), (None, "g")]


def test_the_run_log_counts_the_calls_and_names_the_commit(tmp_path):
    result = run(LLM.offline(cache_dir=CACHE_DIR, ledger_path=tmp_path / "spend.jsonl"))
    record = run_record(started=datetime.now(timezone.utc), seconds=result.seconds, provider="deepseek", model="deepseek-flash",
                        dispositions=[r.disposition for r in result.results], classifications=result.classifications,
                        translations=result.translations, traces=b"")
    assert record["calls"] == {"translate": {"cached": 6, "live": 0}, "classify": {"cached": 31, "live": 0}}
    assert record["retries"] == 0 and record["confidence_fallbacks"] == 0
    assert record["dispositions"] == {"flag": 18, "review": 3, "pass": 11}
    assert set(record["stage_seconds"]) == {"translate", "classify", "retrieve_and_decide", "report"}
    assert record["commit"] is None or re.fullmatch(r"[0-9a-f]{7,}(\+changes)?", record["commit"])
