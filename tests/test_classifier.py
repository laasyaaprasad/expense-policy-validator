"""The classifier's validation, confidence and code-side rules, offline, with a fake provider."""

import json
import math
import re

import pytest

from tests.test_llm import OPENROUTER, FakeClient, response
from validator.classifier import TAXONOMY, classify
from validator.data import load_receipts
from validator.llm import LLM, LLMError

RECEIPTS = {r.receipt_id: r for r in load_receipts()}


def reply(items: list[dict], category_logprob: float = -0.05):
    """A provider reply whose tokens line up with its text; category values get `category_logprob` per token."""
    content = json.dumps({"items": items})
    spans = [(m.start(1), m.end(1)) for m in re.finditer(r'"category": "([^"]*)"', content)]
    tokens = []
    for piece in re.finditer(r"\w+|\W", content):
        inside = any(start <= piece.start() < end for start, end in spans)
        tokens.append((piece[0], category_logprob if inside else 0.0))
    return response(content, tokens)


def line(line_id, category, **fields):
    return {"line_id": line_id, "category": category, "item_kind": None, "recipient_name": None, "recipient_type": None,
            "reason": "test", **fields}


@pytest.fixture
def llm_with(tmp_path):
    def make(*replies):
        client = FakeClient(*replies)
        return LLM(OPENROUTER, client=client, cache_dir=tmp_path / "cache", ledger_path=tmp_path / "spend.jsonl"), client
    return make


def test_restricted_comes_from_the_taxonomy_and_confidence_from_logprobs(llm_with):
    llm, _ = llm_with(reply([line("RCP-027-L1", "clothing"), line("RCP-027-L2", "clothing")], category_logprob=-0.05))
    c = classify(RECEIPTS["RCP-027"], {}, llm)
    item = c.receipt.items["RCP-027-L1"]
    assert item.restricted is TAXONOMY["clothing"] is True
    assert item.confidence == pytest.approx(math.exp(-0.05), abs=1e-6)  # "clothing" is one token here


def test_non_itemized_line_is_other_with_no_call(llm_with):
    llm, client = llm_with()
    c = classify(RECEIPTS["RCP-023"], {}, llm)
    [item] = c.receipt.items.values()
    assert item.category == "other" and item.confidence == 1.0 and item.reason
    assert client.requests == [] and c.cache_key is None


def test_an_unknown_category_is_retried_with_the_problem_quoted(llm_with):
    llm, client = llm_with(reply([line("RCP-032-L1", "saas")]), reply([line("RCP-032-L1", "software_subscription")]))
    c = classify(RECEIPTS["RCP-032"], {}, llm)
    assert c.receipt.items["RCP-032-L1"].category == "software_subscription"
    assert "'saas' is not one of the categories" in client.requests[1]["messages"][-1]["content"]
    assert c.retried_after == ("RCP-032-L1: 'saas' is not one of the categories",)  # kept for the trace


@pytest.mark.parametrize("bad, problem", [
    ([], "line RCP-032-L1 is missing"),
    ([line("RCP-032-L1", "software_subscription"), line("RCP-032-L1", "software_subscription")], "appears 2 times"),
    ([line("RCP-032-L1", "software_subscription", item_kind="mobile_phone")], 'needs category "other"'),
    ([line("RCP-032-L1", "software_subscription", recipient_type="client")], "recipient_name is not"),
])
def test_structural_problems_are_retried(llm_with, bad, problem):
    llm, client = llm_with(reply(bad), reply([line("RCP-032-L1", "software_subscription")]))
    classify(RECEIPTS["RCP-032"], {}, llm)
    assert problem in client.requests[1]["messages"][-1]["content"]


def test_a_second_bad_reply_raises(llm_with):
    llm, _ = llm_with(reply([line("RCP-032-L1", "saas")]), reply([line("RCP-032-L1", "saas")]))
    with pytest.raises(LLMError):
        classify(RECEIPTS["RCP-032"], {}, llm)


def test_unalignable_categories_get_zero_confidence(llm_with):
    # A duplicated key: JSON keeps the last value, the token stream holds both. Rather than guess
    # which tokens decided the answer, every line is distrusted, so each raises EXC-03.
    content = '{"items": [{"line_id": "RCP-032-L1", "category": "other", "category": "software_subscription", "reason": "x"}]}'
    llm, _ = llm_with(response(content, [(content, -0.01)]))
    c = classify(RECEIPTS["RCP-032"], {}, llm)
    assert c.receipt.items["RCP-032-L1"].confidence == 0.0
    assert c.confidence_fallback == "the category values in the reply do not line up with its parsed items"


def test_tokens_that_do_not_match_the_text_give_zero_confidence_not_a_crash(llm_with):
    content = json.dumps({"items": [line("RCP-032-L1", "software_subscription")]})
    llm, _ = llm_with(response(content, [(content[:-1], -0.01), ("x", 0.0)]))
    c = classify(RECEIPTS["RCP-032"], {}, llm)
    assert c.receipt.items["RCP-032-L1"].confidence == 0.0
    assert c.confidence_fallback == "the logprob tokens do not match the reply text"


def test_glosses_and_context_go_into_the_request(llm_with):
    llm, client = llm_with(reply([line(f"RCP-008-L{n}", "food_beverage") for n in (1, 2, 3)]),
                           reply([line(f"RCP-008-L{n}", "food_beverage") for n in (1, 2, 3)]))
    glosses = {"RCP-008-L1": "Nicoise salad", "RCP-008-L2": "Mineral water 50cl", "RCP-008-L3": "Espresso coffee"}
    classify(RECEIPTS["RCP-008"], glosses, llm)
    payload = json.loads(client.requests[0]["messages"][1]["content"])
    assert payload["receipt"]["merchant"] == "Le Petit Marche"
    assert [item["english"] for item in payload["items"]] == list(glosses.values())
    classify(RECEIPTS["RCP-008"], glosses, llm, context=False)
    assert "receipt" not in json.loads(client.requests[1]["messages"][1]["content"])


def test_english_lines_carry_no_gloss(llm_with):
    llm, client = llm_with(reply([line("RCP-032-L1", "software_subscription")]))
    classify(RECEIPTS["RCP-032"], {}, llm)
    assert "english" not in json.loads(client.requests[0]["messages"][1]["content"])["items"][0]


def test_the_request_uses_the_smoke_tested_settings(llm_with):
    llm, client = llm_with(reply([line("RCP-032-L1", "software_subscription")]))
    classify(RECEIPTS["RCP-032"], {}, llm)
    request = client.requests[0]
    assert request["temperature"] == 1.0 and request["logprobs"] is True
    assert request["extra_body"]["reasoning"] == {"enabled": False}


def test_the_prompt_lists_exactly_the_taxonomy():
    from validator.classifier import DEFINITIONS, SYSTEM
    assert set(DEFINITIONS) == set(TAXONOMY)
    assert all(f"- {name}:" in SYSTEM for name in TAXONOMY)


def test_the_prompt_quotes_no_receipt_text():
    # The prompt's examples are our own. A receipt line may appear in it only where the taxonomy's
    # own table of typical examples already gives it ("service charge", "greeting cards").
    from validator.classifier import SYSTEM
    from validator.data import TAXONOMY_PATH
    table = " ".join(row for row in TAXONOMY_PATH.read_text(encoding="utf-8").lower().splitlines() if row.startswith("| `"))
    prompt = SYSTEM.lower()
    leaked = [item.description_raw for r in RECEIPTS.values() for item in r.line_items
              if item.description_raw.lower() in prompt and item.description_raw.lower() not in table]
    assert leaked == []
