"""Put version 4 into the pipeline's lookup: `uv run --group semantic python -m experiments.semantic_v4.export`.

- config.yaml: adds experiment 12's rule ("every word accounted for, by meaning") and its calibrated τ_word, keeping
  the embedding model, the memory and the two-key rule as they are.
- vectors.npz: rebuilt to hold exactly the vectors the pipeline and its offline checks need. These are the sentences
  and words of the library, the memory and the restricted references, and of every line the pipeline, the dev set, the
  reworded receipts and the CORD pilot read. Without the embedding model installed, they replay unchanged, and a line
  or word not in the file sends its line to the model (validator/lookup.py).
"""

import json
from pathlib import Path

import numpy as np
import yaml

from evaluation import cord
from evaluation.dev import load_dev
from experiments.paraphrase.__main__ import load_variants, reword
from validator.data import ROOT, load_receipts
from validator.lookup import LOOKUP_DIR, Embedder, Lookup, item_words
from validator.normaliser import normalise

HERE = Path(__file__).resolve().parent
CACHES = [ROOT / "experiments" / d / "cache" / "vectors.npz" for d in ("semantic_v32", "semantic_v33", "semantic_v4")] + [LOOKUP_DIR / "vectors.npz"]


def main() -> int:
    m = json.loads((HERE / "results" / "metrics.json").read_text(encoding="utf-8"))
    path = LOOKUP_DIR / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["source"] = ("experiments/semantic_v3 (experiment 8) for the model, memory and two-key rule; experiments/semantic_v4 "
                        "(experiment 12) for the memory rule. Recalibrate on reviewed company receipts before production")
    config.update({"memory_rule": m["config"]["memory_rule"], "tau_word": m["config"]["tau_word"], "block_tau": m["config"]["block_tau"]})
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    lookup = Lookup()
    dev, _ = load_dev()
    main_receipts = load_receipts()
    lines = [line.description_raw for rs in (main_receipts, dev, reword(main_receipts, load_variants()), load_receipts(cord.RECEIPTS_PATH))
             for r in rs for line in r.line_items]
    refs = [t for t, _ in lookup.library + lookup.memory + lookup.risk]
    texts = list(dict.fromkeys([normalise(t) for t in refs + lines] + [w for t in refs + lines for w in item_words(t)]))

    emb = Embedder(config["model"], HERE / "cache" / "export-scratch.npz")  # never saved over the caches it reads
    for cache in CACHES:
        emb._cache.update(Embedder(config["model"], cache)._cache)
    emb.save = lambda: None
    vectors = emb.embed(texts)
    missing = [t for t, v in zip(texts, vectors) if v is None]
    if missing:
        raise SystemExit(f"{len(missing)} texts have no vector and the model isn't installed: {missing[:5]}")
    keys = [emb.key(t) for t in texts]
    np.savez_compressed(LOOKUP_DIR / "vectors.npz", keys=np.array(keys), vectors=np.stack(vectors))
    print(f"config: {config['memory_rule']}, tau_word {config['tau_word']}; vectors.npz: {len(keys)} vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
