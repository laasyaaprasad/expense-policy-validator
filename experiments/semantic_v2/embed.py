"""The local embedding model (PLAN.md, step 1): Qwen3-Embedding-0.6B at a pinned revision, on the CPU, in float32.

No API call and no spend: the text never leaves the machine. Vectors are cached by text in
cache/vectors.npz, so the experiment replays without the model installed; `--recompute` embeds every
text again in a fresh process, for the repeatability check (bar 8).

    uv run --group semantic python -m experiments.semantic_v2.embed --recompute OUT.npz TEXTS.json
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE / "cache" / "vectors.npz"
MODEL = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
INSTRUCTION = "Instruct: Identify what kind of product or service this expense receipt line is\nQuery: "
BATCH = 64


def key(text: str) -> str:
    return hashlib.sha256(f"{MODEL}@{REVISION}\n{INSTRUCTION}{text}".encode()).hexdigest()[:24]


def _model():
    import torch
    from sentence_transformers import SentenceTransformer
    torch.manual_seed(0)
    return SentenceTransformer(MODEL, revision=REVISION, device="cpu", model_kwargs={"dtype": torch.float32})


def compute(texts: list[str]) -> np.ndarray:
    """Unit-length vectors straight from the model, one row per text."""
    model = _model()
    vectors = model.encode([INSTRUCTION + t for t in texts], batch_size=BATCH, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32)


class Embedder:
    def __init__(self, cache_path: Path = CACHE_PATH):
        self.cache_path = cache_path
        self._cache: dict[str, np.ndarray] = {}
        if cache_path.exists():
            data = np.load(cache_path)
            self._cache = dict(zip(data["keys"].tolist(), data["vectors"]))

    def embed(self, texts: list[str]) -> np.ndarray:
        missing = list(dict.fromkeys(t for t in texts if key(t) not in self._cache))
        if missing:
            for text, vector in zip(missing, compute(missing)):
                self._cache[key(text)] = vector
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            keys = sorted(self._cache)
            np.savez_compressed(self.cache_path, keys=np.array(keys), vectors=np.stack([self._cache[k] for k in keys]))
        return np.stack([self._cache[key(t)] for t in texts])


if __name__ == "__main__":
    if sys.argv[1:2] != ["--recompute"]:
        sys.exit(__doc__)
    out, texts = Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    np.save(out, compute(texts))
