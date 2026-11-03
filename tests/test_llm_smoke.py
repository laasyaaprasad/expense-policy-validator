"""Live checks of the capabilities the classifier relies on, checked with real calls.

Run with `make smoke`; needs the key in .env. Responses are cached under cache/smoke/ and billed
in outputs/spend.jsonl, so a rerun makes no calls. Items are written here, never taken from receipts.

Finding: at temperature 0 DeepSeek reports logprobs after greedy decoding (0.0 for every chosen
token, -9999 for every alternative), so they carry no confidence. At temperature 1 they are the
model's own distribution. The classifier therefore samples at temperature 1 (DECISIONS.md, section 4).
"""

import json
import re

import pytest

from validator.llm import CACHE_DIR, LEDGER_PATH, LLM, ledger_total
from validator.taxonomy import load_taxonomy

pytestmark = pytest.mark.llm

CATEGORIES = sorted(load_taxonomy())
ITEMS = [("S1", "Printer paper A4, 500 sheets"), ("S2", "Moscow Mule mug")]
PROMPT_VERSION = "smoke-v1-t1"
CLASSIFY = {"logprobs": True, "top_logprobs": 5, "temperature": 1.0}  # the classifier's settings
MESSAGES = [
    {"role": "system", "content": (
        "Classify each expense line item into exactly one category from this list: "
        + ", ".join(CATEGORIES)
        + ". Classify what the item is, not the words in its name. Reply in json only, in this format: "
        '{"items": [{"line_id": "...", "category": "...", "reason": "one short sentence"}]}. '
        "Give category before reason."
    )},
    {"role": "user", "content": json.dumps({"items": [{"line_id": i, "description": d} for i, d in ITEMS]})},
]


def ledger_entries() -> int:
    return len(LEDGER_PATH.read_text().splitlines()) if LEDGER_PATH.exists() else 0


@pytest.fixture(scope="module")
def llm():
    return LLM()


@pytest.fixture(scope="module")
def completion(llm):
    return llm.complete(MESSAGES, purpose="smoke", prompt_version=PROMPT_VERSION, **CLASSIFY)


@pytest.fixture(scope="module")
def greedy(llm):
    return llm.complete(MESSAGES, purpose="smoke", prompt_version="smoke-v1", logprobs=True, top_logprobs=5)


def category_spans(content: str) -> list[tuple[str, int, int]]:
    return [(m[1], m.start(1), m.end(1)) for m in re.finditer(r'"category"\s*:\s*"([^"]*)"', content)]


def test_json_output_parses_and_categories_are_in_the_taxonomy(completion):
    items = json.loads(completion.content)["items"]
    assert [item["line_id"] for item in items] == [line_id for line_id, _ in ITEMS]
    assert all(item["category"] in CATEGORIES for item in items)
    assert list(items[0])[:2] == ["line_id", "category"]  # category comes before the reason
    print("\ncategories:", {item["line_id"]: item["category"] for item in items})


def test_logprobs_line_up_with_the_category_tokens(completion):
    assert completion.tokens is not None
    assert "".join(token.token for token in completion.tokens) == completion.content
    probabilities = {category: completion.span_probability(start, end) for category, start, end in category_spans(completion.content)}
    assert len(probabilities) == len(ITEMS)
    assert all(0 < p <= 1 for p in probabilities.values())
    print("\ncategory probabilities:", {c: round(p, 6) for c, p in probabilities.items()})


def test_logprobs_carry_information_at_temperature_one(completion):
    # Some chosen token is below certainty, and some alternative has a real probability.
    assert any(token.logprob < 0 for token in completion.tokens)
    assert any(-50 < logprob < 0 for token in completion.tokens for _, logprob in token.top[1:])


def test_logprobs_are_degenerate_at_temperature_zero(greedy):
    # Why the classifier does not run at temperature 0. If this starts failing, revisit the choice.
    assert {token.logprob for token in greedy.tokens} == {0.0}
    assert {logprob for token in greedy.tokens for _, logprob in token.top[1:]} == {-9999.0}


def test_thinking_is_off_and_the_temperature_is_honoured(completion):
    details = completion.usage.get("completion_tokens_details") or {}
    assert not details.get("reasoning_tokens")
    record = json.loads((CACHE_DIR / "smoke" / f"{completion.cache_key[:24]}.json").read_text())
    assert not record["response"]["reasoning_content"]
    assert record["request"]["params"]["temperature"] == CLASSIFY["temperature"]


def test_the_model_is_reported(completion):
    # DeepSeek's own API has no upstream routing; the model ID it reports is what we record.
    assert completion.model
    print("\nmodel reported:", completion.model)


def test_a_second_identical_call_is_a_cache_hit(llm, completion):
    before = ledger_entries()
    again = llm.complete(MESSAGES, purpose="smoke", prompt_version=PROMPT_VERSION, **CLASSIFY)
    assert again.cached and again.content == completion.content
    assert ledger_entries() == before


def test_a_fresh_repeat_gives_the_same_categories_on_clear_items(llm, completion):
    # Sampling at temperature 1 can only change an answer the model is unsure of; these two are clear.
    repeat = llm.complete(MESSAGES, purpose="smoke", prompt_version=PROMPT_VERSION + "-repeat", **CLASSIFY)
    assert [c for c, _, _ in category_spans(repeat.content)] == [c for c, _, _ in category_spans(completion.content)]


def test_smoke_spend_is_under_a_cent(completion):
    assert ledger_total(purpose="smoke") < 0.01
    print(f"\nsmoke spend so far: ${ledger_total(purpose='smoke'):.6f}")
