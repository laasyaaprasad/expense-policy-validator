"""Embeddings through OpenRouter, pinned to DeepInfra, cached on disk, with this experiment's own spend ledger.

Each vector is cached by model, prefix and text in cache/embeddings/<model>.npz, so a rerun makes no calls and
gets the same vectors. The three candidates (PLAN.md) all have open weights, so the same stage could
run locally in production.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from validator.data import ROOT

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache" / "embeddings"  # one file per model: their vectors differ in length
LEDGER_PATH = HERE / "spend.jsonl"
BASE_URL = "https://openrouter.ai/api/v1"
HOST = "DeepInfra"
BATCH = 64


@dataclass(frozen=True)
class Model:
    name: str
    prefix: str  # the same on both sides: lines and anchors are matched symmetrically


CANDIDATES = (
    Model("intfloat/multilingual-e5-large", "query: "),
    Model("baai/bge-m3", ""),
    Model("qwen/qwen3-embedding-8b", "Instruct: Identify what kind of product or service this expense receipt line is\nQuery: "),
)
TIE_BREAK = "baai/bge-m3"  # PLAN.md


class EmbeddingError(RuntimeError):
    pass


def text_key(model: Model, text: str) -> str:
    return hashlib.sha256(f"{model.name}\n{model.prefix}{text}".encode()).hexdigest()[:24]


class Embedder:
    def __init__(self, *, cache_dir: Path = CACHE_DIR, ledger_path: Path = LEDGER_PATH, client=None):
        self.cache_dir, self.ledger_path, self._client = cache_dir, ledger_path, client
        self._vectors: dict[str, dict[str, np.ndarray]] = {}  # model → text key → vector

    def _path(self, model: Model) -> Path:
        return self.cache_dir / (model.name.replace("/", "__") + ".npz")

    def _cache(self, model: Model) -> dict[str, np.ndarray]:
        if model.name not in self._vectors:
            path = self._path(model)
            data = np.load(path) if path.exists() else None
            self._vectors[model.name] = dict(zip(data["keys"].tolist(), data["vectors"])) if data is not None else {}
        return self._vectors[model.name]

    def embed(self, model: Model, texts: list[str]) -> np.ndarray:
        """Unit-length vectors, one row per text, in the order given."""
        cache = self._cache(model)
        keys = [text_key(model, text) for text in texts]
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in cache))
        for start in range(0, len(missing), BATCH):
            batch = missing[start:start + BATCH]
            response = self._get_client().embeddings.create(
                model=model.name, input=[model.prefix + text for text in batch],
                extra_body={"provider": {"order": [HOST], "allow_fallbacks": False}})
            data = sorted(response.data, key=lambda item: item.index)
            if len(data) != len(batch):
                raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(data)}")
            for text, item in zip(batch, data):
                vector = np.asarray(item.embedding, dtype=np.float32)
                cache[text_key(model, text)] = vector / np.linalg.norm(vector)
            self._log(model, response, len(batch))
        if missing:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            ordered = sorted(cache)
            np.savez_compressed(self._path(model), keys=np.array(ordered), vectors=np.stack([cache[k] for k in ordered]))
        return np.stack([cache[key] for key in keys])

    def _get_client(self):
        if self._client is None:
            load_dotenv(ROOT / ".env", override=False)
            key = os.environ.get("OPENROUTER_API_KEY", "")
            if not key:
                raise EmbeddingError("cache miss and OPENROUTER_API_KEY is not set: these texts were never embedded")
            from openai import OpenAI
            self._client = OpenAI(api_key=key, base_url=BASE_URL, max_retries=5, timeout=120)
        return self._client

    def _log(self, model: Model, response, inputs: int) -> None:
        usage = response.usage.model_dump() if response.usage else {}
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": "embed", "model": model.name, "provider": getattr(response, "provider", None), "inputs": inputs,
            "prompt_tokens": usage.get("prompt_tokens", 0), "usd": float(usage["cost"]) if "cost" in usage else None,
        }
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
