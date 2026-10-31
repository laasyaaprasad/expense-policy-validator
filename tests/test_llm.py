"""The client's cache, ledger, request shape and logprob alignment, offline, with a fake
provider that returns real OpenAI SDK response objects. No key and no network."""

import json
import math

import pytest
from openai.types.chat import ChatCompletion

from validator.llm import LLM, LLMError, Settings, cost_usd

DEEPSEEK = Settings(provider="deepseek", model="deepseek-flash", base_url="https://api.deepseek.com", api_key="sk-test-SECRET")
OPENROUTER = Settings(provider="openrouter", model="deepseek/deepseek-v4.1-flash", base_url="https://openrouter.ai/api/v1",
                      api_key="sk-or-test-SECRET", pinned=("google-vertex",))
MESSAGES = [{"role": "system", "content": "Reply in json."}, {"role": "user", "content": "Printer paper"}]


def response(content: str, tokens: list[tuple[str, float]] | None = None, usage: dict | None = None) -> ChatCompletion:
    logprobs = None if tokens is None else {"content": [
        {"token": token, "logprob": logprob, "bytes": None, "top_logprobs": [{"token": token, "logprob": logprob, "bytes": None}]}
        for token, logprob in tokens
    ]}
    return ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": "deepseek-flash", "system_fingerprint": "fp_test",
        "choices": [{"index": 0, "finish_reason": "stop", "logprobs": logprobs,
                     "message": {"role": "assistant", "content": content}}],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
                           "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 600},
    })


class FakeClient:
    """Stands in for openai.OpenAI: records each request and replies from a queue."""

    def __init__(self, *replies: ChatCompletion):
        self.replies, self.requests = list(replies), []
        self.chat = self
        self.completions = self

    def create(self, **params):
        self.requests.append(params)
        return self.replies.pop(0)


@pytest.fixture
def paths(tmp_path):
    return {"cache_dir": tmp_path / "cache", "ledger_path": tmp_path / "spend.jsonl"}


def ledger(paths) -> list[dict]:
    path = paths["ledger_path"]
    return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []


def test_second_identical_call_is_a_cache_hit(paths):
    client = FakeClient(response('{"category": "office_supplies"}'))
    llm = LLM(DEEPSEEK, client=client, **paths)
    first = llm.complete(MESSAGES, purpose="test", prompt_version="v1")
    second = llm.complete(MESSAGES, purpose="test", prompt_version="v1")
    assert not first.cached and second.cached
    assert second.content == first.content and second.usage == first.usage
    assert len(client.requests) == 1 and len(ledger(paths)) == 1


@pytest.mark.parametrize("change", [{"prompt_version": "v2"}, {"messages": MESSAGES[:1]}])
def test_a_changed_prompt_or_input_misses_the_cache(paths, change):
    client = FakeClient(response('{"a": 1}'), response('{"a": 2}'))
    llm = LLM(DEEPSEEK, client=client, **paths)
    llm.complete(MESSAGES, purpose="test", prompt_version="v1")
    args = {"messages": MESSAGES, "prompt_version": "v1", **change}
    assert llm.complete(args["messages"], purpose="test", prompt_version=args["prompt_version"]).content == '{"a": 2}'
    assert len(client.requests) == 2


def test_ledger_records_tokens_and_dollars(paths):
    LLM(DEEPSEEK, client=FakeClient(response("{}")), **paths).complete(MESSAGES, purpose="test", prompt_version="v1")
    [entry] = ledger(paths)
    # 400 cached at $0.006/M, 600 uncached at $0.30/M, 100 output at $1.20/M (peak rates).
    assert entry["usd"] == pytest.approx((400 * 0.006 + 600 * 0.30 + 100 * 1.20) / 1e6)
    assert entry["prompt_tokens"] == 1000 and entry["completion_tokens"] == 100 and entry["reasoning_tokens"] == 0


def test_openrouter_reports_its_own_cost():
    assert cost_usd("openrouter", "any", {"cost": 0.0123}) == 0.0123
    assert cost_usd("deepseek", "unknown-model", {"prompt_tokens": 5}) is None


def test_blank_json_content_is_retried_and_every_attempt_is_billed(paths):
    client = FakeClient(response("   "), response('{"ok": true}'))
    completion = LLM(DEEPSEEK, client=client, **paths).complete(MESSAGES, purpose="test", prompt_version="v1")
    assert completion.content == '{"ok": true}'
    assert [e["attempt"] for e in ledger(paths)] == [1, 2]


def test_persistent_blank_content_raises(paths):
    client = FakeClient(*(response(" ") for _ in range(3)))
    with pytest.raises(LLMError):
        LLM(DEEPSEEK, client=client, **paths).complete(MESSAGES, purpose="test", prompt_version="v1")
    assert not (paths["cache_dir"] / "test").exists()


def test_deepseek_request_turns_thinking_off_and_asks_for_json():
    params = LLM(DEEPSEEK, client=FakeClient()).request(MESSAGES, logprobs=True, top_logprobs=5)
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}
    assert params["response_format"] == {"type": "json_object"}
    assert params["temperature"] == 0 and params["logprobs"] is True and params["top_logprobs"] == 5


def test_openrouter_request_pins_the_provider_and_uses_strict_schema():
    schema = {"type": "object", "properties": {"category": {"type": "string", "enum": ["a", "b"]}}}
    params = LLM(OPENROUTER, client=FakeClient()).request(MESSAGES, schema=schema)
    assert params["response_format"]["json_schema"] == {"name": "result", "strict": True, "schema": schema}
    assert params["extra_body"]["provider"] == {"require_parameters": True, "order": ["google-vertex"], "allow_fallbacks": False}
    assert params["extra_body"]["reasoning"] == {"enabled": False}


def test_span_probability_lines_tokens_up_with_the_content(paths):
    content = '{"category": "office_supplies"}'
    tokens = [('{"', -0.0), ("category", -0.0), ('":', -0.0), (' "', -0.0), ("office", -0.2), ("_supplies", -0.1), ('"}', -0.0)]
    completion = LLM(DEEPSEEK, client=FakeClient(response(content, tokens)), **paths).complete(
        MESSAGES, purpose="test", prompt_version="v1", logprobs=True)
    start = content.index("office")
    assert completion.span_probability(start, start + len("office_supplies")) == pytest.approx(math.exp(-0.3))


def test_span_probability_allows_a_final_newline_the_content_lacks(paths):
    # Seen on CORD-V048: the logprobs end with "\n", the content does not.
    tokens = [('{"', -0.0), ("a", -0.4), ('": 1}', -0.0), ("\n", -0.0)]
    completion = LLM(DEEPSEEK, client=FakeClient(response('{"a": 1}', tokens)), **paths).complete(
        MESSAGES, purpose="test", prompt_version="v1", logprobs=True)
    assert completion.span_probability(2, 3) == pytest.approx(math.exp(-0.4))


def test_span_probability_refuses_tokens_that_do_not_line_up(paths):
    completion = LLM(DEEPSEEK, client=FakeClient(response('{"a": 1}', [("{", -0.1)])), **paths).complete(
        MESSAGES, purpose="test", prompt_version="v1", logprobs=True)
    with pytest.raises(LLMError):
        completion.span_probability(0, 1)


def test_the_key_is_never_written_or_printed(paths):
    llm = LLM(DEEPSEEK, client=FakeClient(response("{}")), **paths)
    llm.complete(MESSAGES, purpose="test", prompt_version="v1")
    written = "".join(p.read_text() for p in paths["cache_dir"].rglob("*.json")) + paths["ledger_path"].read_text()
    assert "SECRET" not in written
    assert "SECRET" not in repr(DEEPSEEK)


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr("validator.llm.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        Settings.from_env()


# --- complete_json and offline replay -----------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402

from validator.llm import complete_json  # noqa: E402


class Reply(BaseModel):
    items: list[str]


def test_complete_json_retries_once_with_the_problem_quoted(paths):
    client = FakeClient(response('{"items": "not a list"}'), response('{"items": ["a"]}'))
    parsed, _ = complete_json(LLM(DEEPSEEK, client=client, **paths), MESSAGES, Reply, purpose="test", prompt_version="v1")
    assert parsed.items == ["a"]
    retry_messages = client.requests[1]["messages"]
    assert retry_messages[-2]["role"] == "assistant" and "problems" in retry_messages[-1]["content"]


def test_complete_json_applies_the_callers_check(paths):
    client = FakeClient(response('{"items": []}'), response('{"items": ["L1"]}'))
    check = lambda parsed: [] if parsed.items else ["line L1 is missing"]
    parsed, _ = complete_json(LLM(DEEPSEEK, client=client, **paths), MESSAGES, Reply, purpose="test", prompt_version="v1", check=check)
    assert parsed.items == ["L1"]
    assert "line L1 is missing" in client.requests[1]["messages"][-1]["content"]


def test_complete_json_gives_up_after_one_retry(paths):
    client = FakeClient(response('{"items": 1}'), response('{"items": 2}'))
    with pytest.raises(LLMError, match="after a retry"):
        complete_json(LLM(DEEPSEEK, client=client, **paths), MESSAGES, Reply, purpose="test", prompt_version="v1")


def test_offline_mode_replays_the_cache_and_refuses_misses(paths, monkeypatch):
    monkeypatch.setattr("validator.llm.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    LLM(DEEPSEEK, client=FakeClient(response('{"a": 1}')), **paths).complete(MESSAGES, purpose="test", prompt_version="v1")
    offline = LLM.offline(**paths)
    assert offline.complete(MESSAGES, purpose="test", prompt_version="v1").content == '{"a": 1}'
    with pytest.raises(LLMError, match="offline"):
        offline.complete(MESSAGES, purpose="test", prompt_version="v2")
