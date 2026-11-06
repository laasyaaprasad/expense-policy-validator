"""Vercel AI Gateway's Decision API, cached on disk, with this experiment's own spend ledger.

A `predicate` question returns the probability that the answer is yes. Each request is cached under
cache/ by a hash of its body, so a rerun makes no calls and gets the same answers. The model is
pinned (PLAN.md: `baseten/mammoth-1`), and the request allows no other model, so a cached answer
always came from the same one. Spend goes to this experiment's spend.jsonl; the key
(AI_GATEWAY_API_KEY in .env) is never written to the cache or the ledger.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from validator.data import ROOT

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
LEDGER_PATH = HERE / "spend.jsonl"
ENDPOINT = "https://ai-gateway.vercel.sh/v1/decisions"
MODEL = "baseten/mammoth-1"


class DecisionError(RuntimeError):
    pass


def _post(body: dict, key: str) -> dict:
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(12):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 11:  # a free-tier team gets 5 requests a minute: wait it out
                time.sleep(int(error.headers.get("retry-after") or 60) + 1)
                continue
            if error.code in (500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise DecisionError(f"the gateway returned {error.code}: {error.read().decode(errors='replace')[:300]}") from None
    raise DecisionError("unreachable")


class Decisions:
    def __init__(self, *, model: str = MODEL, cache_dir: Path = CACHE_DIR, ledger_path: Path = LEDGER_PATH, post=None):
        self.model, self.cache_dir, self.ledger_path = model, cache_dir, ledger_path
        self._post = post  # injectable for tests; the real one needs AI_GATEWAY_API_KEY

    def ask(self, text: str, questions: list[dict], *, purpose: str) -> dict:
        body = {"model": self.model, "input": text, "questions": questions,
                "providerOptions": {"gateway": {"models": [self.model]}}}  # no fallback to another model
        key = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        started = time.monotonic()
        response = self._poster()(body)
        seconds = time.monotonic() - started
        if response.get("model") != self.model:
            raise DecisionError(f"asked {self.model}, answered by {response.get('model')}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"request": body, "response": response}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        usage = response.get("usage") or {}
        gateway = (response.get("provider_metadata") or {}).get("gateway") or {}
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "purpose": purpose, "cache_key": key,
                "model": response.get("model"), "provider": (gateway.get("routing") or {}).get("finalProvider"),
                "input_tokens": usage.get("input_tokens", 0), "seconds": round(seconds, 3),
                "usd": float(gateway["cost"]) if "cost" in gateway else None,
            }) + "\n")
        return response

    def _poster(self):
        if self._post is None:
            load_dotenv(ROOT / ".env", override=False)
            key = os.environ.get("AI_GATEWAY_API_KEY", "")
            if not key:
                raise DecisionError("cache miss and AI_GATEWAY_API_KEY is not set: this request was never run")
            self._post = lambda body: _post(body, key)
        return self._post


def probability(response: dict, name: str) -> float | None:
    """The yes-probability of the named predicate, or None if the model declined to answer it."""
    answer = next(a for a in response["answers"] if a.get("name") == name)
    if answer["type"] == "refusal":
        return None
    if answer["type"] != "predicate" or not 0 <= answer["probability"] <= 1:
        raise DecisionError(f"not a predicate answer: {answer}")
    return float(answer["probability"])
