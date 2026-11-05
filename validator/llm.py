"""The only place the pipeline talks to a model: the provider client, a disk cache and a spend ledger.

Every response is cached under cache/<purpose>/, keyed on the full request (model, provider
settings, prompt version, messages, parameters). A rerun therefore makes no calls and returns the
same text, so a change in results always has a cause we can name. Every real call appends its
tokens and dollars to outputs/spend.jsonl. Neither file ever holds the API key.

The provider is OpenRouter, the brief's: `json_object` output, reasoning off, and the upstream
pinned to hosts that return logprobs. Unpinned, OpenRouter routed a smoke call to a host that
dropped them. `json_object` is not strict `json_schema`, so callers validate what comes back.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from validator.data import ROOT

CACHE_DIR = ROOT / "cache"
LEDGER_PATH = ROOT / "outputs" / "spend.jsonl"
BLANK_RETRIES = 3  # JSON mode can return blank content; a retry gets a real reply

PROVIDER = "openrouter"  # recorded in every result and part of every cache key
BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "deepseek/deepseek-v4.1-flash"
# The OpenRouter upstreams that returned real logprobs in the smoke test, in order.
PINNED = ("Fireworks", "Together")


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    pinned: tuple[str, ...] = ()  # OpenRouter upstream providers, in order

    @classmethod
    def from_env(cls, require_key: bool = True) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key and require_key:
            raise LLMError("OPENROUTER_API_KEY is not set; put it in .env")
        pinned = tuple(p.strip() for p in os.environ.get("OPENROUTER_PROVIDER", "").split(",") if p.strip()) or PINNED
        return cls(
            provider=PROVIDER,
            model=os.environ.get("OPENROUTER_MODEL") or MODEL,
            base_url=os.environ.get("OPENROUTER_BASE_URL") or BASE_URL,
            api_key=key,
            pinned=pinned,
        )


@dataclass(frozen=True)
class Token:
    token: str
    logprob: float
    top: tuple[tuple[str, float], ...] = ()  # the most likely alternatives at this position


@dataclass(frozen=True)
class Completion:
    content: str
    tokens: tuple[Token, ...] | None  # None when logprobs were not requested
    model: str  # as the provider reports it
    usage: dict[str, Any]
    cache_key: str
    cached: bool
    retried_after: tuple[str, ...] = ()  # the first reply's problems, when this is the retry that fixed them

    def span_probability(self, start: int, end: int) -> float:
        """Probability of the tokens covering content[start:end]: exp of their summed logprobs."""
        if self.tokens is None:
            raise LLMError("logprobs were not requested for this completion")
        offset, total, covered = 0, 0.0, False
        for token in self.tokens:
            token_end = offset + len(token.token)
            if token_end > start and offset < end:
                total += token.logprob
                covered = True
            offset = token_end
        # A reply can leave a final newline out of the content that its logprobs still include, so
        # trailing whitespace is allowed.
        joined = "".join(token.token for token in self.tokens)
        if not covered or not joined.startswith(self.content) or joined[len(self.content):].strip():
            raise LLMError("tokens do not line up with the content")
        return math.exp(total)


def cost_usd(usage: dict[str, Any]) -> float | None:
    """What OpenRouter billed for the call, as it reports it; None if the reply carries no charge."""
    return float(usage["cost"]) if "cost" in usage else None


class _OfflineClient:
    """Stands in for the provider when there is no API access: any cache miss is an error."""

    def __init__(self):
        self.chat = self.completions = self

    def create(self, **params):
        raise LLMError("cache miss in offline mode: this request was never run and recorded")


class LLM:
    @classmethod
    def offline(cls, **paths) -> "LLM":
        """Replays the committed cache with no key and no network. Reviewers rerun the pipeline this way."""
        return cls(Settings.from_env(require_key=False), client=_OfflineClient(), **paths)

    @classmethod
    def or_offline(cls) -> "LLM":
        """A live client when a key is set; otherwise the committed cache, where any miss is an error."""
        try:
            return cls()
        except LLMError as e:
            print(f"{e}. Replaying the committed cache instead.")
            return cls.offline()

    def __init__(self, settings: Settings | None = None, *, cache_dir: Path = CACHE_DIR,
                 ledger_path: Path = LEDGER_PATH, client=None):
        self.settings = settings or Settings.from_env()
        self.cache_dir, self.ledger_path = cache_dir, ledger_path
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url, max_retries=5, timeout=120)
        self._client = client

    def request(self, messages: list[dict], *, logprobs: bool = False, top_logprobs: int = 0, max_tokens: int = 2000, temperature: float = 0) -> dict:
        """The exact request sent, which is also what the cache key is computed from."""
        s = self.settings
        params: dict[str, Any] = {"model": s.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if logprobs:
            params.update(logprobs=True, top_logprobs=top_logprobs)
        params["response_format"] = {"type": "json_object"}
        provider = {"require_parameters": True}
        if s.pinned:
            provider.update(order=list(s.pinned), allow_fallbacks=False)
        params["extra_body"] = {"provider": provider, "reasoning": {"enabled": False}}
        return params

    def complete(self, messages: list[dict], *, purpose: str, prompt_version: str, **options) -> Completion:
        params = self.request(messages, **options)
        identity = {"provider": self.settings.provider, "prompt_version": prompt_version, "params": params}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = self.cache_dir / purpose / f"{key[:24]}.json"
        if path.exists():
            return self._completion(json.loads(path.read_text(encoding="utf-8")), key, cached=True)

        for attempt in range(1, BLANK_RETRIES + 1):
            response = self._client.chat.completions.create(**params)
            record = self._record(response, identity)
            self._log_spend(record, purpose, prompt_version, key, attempt)
            if record["response"]["content"].strip():
                break
        else:
            raise LLMError(f"blank content {BLANK_RETRIES} times in a row ({purpose}, {prompt_version})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return self._completion(record, key, cached=False)

    # --- internals ----------------------------------------------------------------------------------

    @staticmethod
    def _record(response, identity: dict) -> dict:
        choice = response.choices[0]
        tokens = None
        if choice.logprobs is not None and choice.logprobs.content is not None:
            tokens = [
                {"token": t.token, "logprob": t.logprob, "top": [[alt.token, alt.logprob] for alt in (t.top_logprobs or [])]}
                for t in choice.logprobs.content
            ]
        message = choice.message.model_dump()
        return {
            "request": identity,
            "response": {
                "content": choice.message.content or "",
                "reasoning_content": message.get("reasoning_content"),
                "tokens": tokens,
                "finish_reason": choice.finish_reason,
                "model": response.model,
                "system_fingerprint": response.system_fingerprint,
                "provider": getattr(response, "provider", None),
                "usage": response.usage.model_dump() if response.usage else {},
            },
        }

    @staticmethod
    def _completion(record: dict, key: str, cached: bool) -> Completion:
        r = record["response"]
        tokens = None if r["tokens"] is None else tuple(
            Token(t["token"], t["logprob"], tuple((alt, lp) for alt, lp in t["top"])) for t in r["tokens"]
        )
        return Completion(content=r["content"], tokens=tokens, model=r["model"], usage=r["usage"], cache_key=key, cached=cached)

    def _log_spend(self, record: dict, purpose: str, prompt_version: str, key: str, attempt: int) -> None:
        usage = record["response"]["usage"]
        details = usage.get("completion_tokens_details") or {}
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": purpose,
            "prompt_version": prompt_version,
            "provider": self.settings.provider,
            "model": record["response"]["model"],
            "cache_key": key[:24],
            "attempt": attempt,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": details.get("reasoning_tokens") or 0,
            "usd": cost_usd(usage),
        }
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def complete_json(llm: LLM, messages: list[dict], model: type[BaseModel], *, purpose: str, prompt_version: str,
                  check=None, **options) -> tuple[BaseModel, Completion]:
    """A completion parsed into `model`. `check(parsed)` returns a list of problems, such as a line
    that is missing. On a parse error or a problem the request is retried once with the problem
    quoted back, which is a different request and so a different cache entry."""
    completion = llm.complete(messages, purpose=purpose, prompt_version=prompt_version, **options)
    problems = _problems(completion, model, check)
    if not problems:
        return model.model_validate_json(completion.content), completion
    retry = [*messages, {"role": "assistant", "content": completion.content},
             {"role": "user", "content": "That reply had problems: " + "; ".join(problems) + ". Reply again in json, fixing them."}]
    completion = llm.complete(retry, purpose=purpose, prompt_version=prompt_version, **options)
    if still := _problems(completion, model, check):
        raise LLMError(f"{purpose} reply still invalid after a retry: {'; '.join(still)}")
    return model.model_validate_json(completion.content), replace(completion, retried_after=tuple(problems))


def _problems(completion: Completion, model: type[BaseModel], check) -> list[str]:
    try:
        parsed = model.model_validate_json(completion.content)
    except ValidationError as error:
        return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
    return list(check(parsed)) if check else []


def ledger_total(path: Path = LEDGER_PATH, purpose: str | None = None) -> float:
    if not path.exists():
        return 0.0
    entries = [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]
    return sum(e["usd"] or 0.0 for e in entries if purpose is None or e["purpose"] == purpose)
