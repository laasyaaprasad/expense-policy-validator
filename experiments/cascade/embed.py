"""Embeddings through OpenRouter, cached on disk, with the experiment's own spend ledger.

Every vector is cached by model and text in cache/embeddings.npz, so a rerun makes no calls and gets
the same vectors. Calls are pinned to the one host that serves the model, with no fallback, so a
cached vector always came from the same place. Spend goes to this experiment's spend.jsonl, not to
outputs/spend.jsonl, which the README's Spend line is checked against.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from validator.data import ROOT

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE / "cache" / "embeddings.npz"
LEDGER_PATH = HERE / "spend.jsonl"
BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "intfloat/multilingual-e5-large"
HOST = "DeepInfra"  # the only OpenRouter host for this model
PREFIX = "query: "  # e5 models expect it; the same prefix on both sides suits a symmetric match
BATCH = 64


class EmbeddingError(RuntimeError):
    pass


def text_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{PREFIX}{text}".encode()).hexdigest()[:24]


class Embedder:
    def __init__(self, *, model: str = MODEL, cache_path: Path = CACHE_PATH, ledger_path: Path = LEDGER_PATH, client=None):
        self.model, self.cache_path, self.ledger_path = model, cache_path, ledger_path
        self._client = client
        self._vectors: dict[str, np.ndarray] = {}
        if cache_path.exists():
            data = np.load(cache_path)
            self._vectors = dict(zip(data["keys"].tolist(), data["vectors"]))

    def embed(self, texts: list[str]) -> np.ndarray:
        """Unit-length vectors, one row per text, in the order given."""
        keys = [text_key(self.model, text) for text in texts]
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in self._vectors))
        for start in range(0, len(missing), BATCH):
            batch = missing[start:start + BATCH]
            response = self._get_client().embeddings.create(
                model=self.model, input=[PREFIX + text for text in batch],
                extra_body={"provider": {"order": [HOST], "allow_fallbacks": False}})
            data = sorted(response.data, key=lambda item: item.index)
            if len(data) != len(batch):
                raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(data)}")
            for text, item in zip(batch, data):
                vector = np.asarray(item.embedding, dtype=np.float32)
                self._vectors[text_key(self.model, text)] = vector / np.linalg.norm(vector)
            self._log(response, len(batch))
        if missing:
            self._save()
        return np.stack([self._vectors[key] for key in keys])

    def _get_client(self):
        if self._client is None:
            load_dotenv(ROOT / ".env", override=False)
            key = os.environ.get("OPENROUTER_API_KEY", "")
            if not key:
                raise EmbeddingError("cache miss and OPENROUTER_API_KEY is not set: these texts were never embedded")
            from openai import OpenAI
            self._client = OpenAI(api_key=key, base_url=BASE_URL, max_retries=5, timeout=120)
        return self._client

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted(self._vectors)
        np.savez_compressed(self.cache_path, keys=np.array(keys), vectors=np.stack([self._vectors[k] for k in keys]))

    def _log(self, response, inputs: int) -> None:
        usage = response.usage.model_dump() if response.usage else {}
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": "embed",
            "model": getattr(response, "model", self.model),
            "provider": getattr(response, "provider", None),
            "inputs": inputs,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "usd": float(usage["cost"]) if "cost" in usage else None,
        }
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def ledger(path: Path = LEDGER_PATH) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]
