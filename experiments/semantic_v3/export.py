"""Export what experiment 8 settled into rules/lookup/, for the pipeline: `uv run python -m experiments.semantic_v3.export`.

- config.yaml: the embedding model and the thresholds calibrated in experiment 8 (results/metrics.json).
- library.csv: the curated library as experiment 8 used it, decontaminated against every test line.
- memory.csv: what the stream learned from the 400 reviewed CORD training receipts, under the two-key rule
  (results/stream.csv): unrestricted lines to remember, and restricted lines kept as risk references. Nothing
  from the 32 receipts, the dev set, the reworded lines or CORD's validation and test splits.
"""

import csv
import json
from pathlib import Path

import yaml

from evaluation import cord
from evaluation.dev import load_dev
from experiments.paraphrase.__main__ import load_variants, reword
from experiments.semantic_first.semantic import TAXONOMY, decontaminate
from experiments.semantic_v2.embed import INSTRUCTION, MODEL, REVISION
from experiments.semantic_v2.lookup import load_library
from experiments.semantic_v3.system import TWO_KEY_CONFIDENCE
from validator.data import ROOT, load_receipts
from validator.normaliser import normalise

HERE = Path(__file__).resolve().parent
OUT = ROOT / "rules" / "lookup"


def main() -> int:
    m = json.loads((HERE / "results" / "metrics.json").read_text(encoding="utf-8"))
    p = m["params"]
    OUT.mkdir(parents=True, exist_ok=True)
    config = {
        "source": "experiments/semantic_v3 (experiment 8); recalibrate on reviewed company receipts before production",
        "model": {"name": MODEL, "revision": REVISION, "instruction": INSTRUCTION},
        "tau_mem": round(p["tau_mem"], 6), "tau_fam": p["tau_fam"], "delta": p["delta"], "safe_cutoff": round(p["safe_cutoff"], 6),
        "memory_confidence": 0.995, "curated_confidence": p["curated_calibration"]["confidence"],
        "two_key_confidence": TWO_KEY_CONFIDENCE,
    }
    (OUT / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    main_receipts = load_receipts()
    dev, _ = load_dev()
    tests = [line.description_raw for rs in (main_receipts, dev, reword(main_receipts, load_variants()), load_receipts(cord.RECEIPTS_PATH),
                                             load_receipts(ROOT / "cord" / "test" / "receipts.jsonl"), load_receipts(ROOT / "cord" / "train" / "receipts.jsonl"))
             for r in rs for line in r.line_items]
    curated, _ = decontaminate(load_library(), tests)
    with (OUT / "library.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "language", "text", "source"])
        w.writerows((a.category, a.language, a.text, a.source) for a in curated)

    memory, seen = [], set()
    with (HERE / "results" / "stream.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            text, label = normalise(row["text"]), row["label"]
            if row["label_sure"] != "True" or text in seen:
                continue
            if TAXONOMY[label]:
                role = "risk"
            elif row["M"] == label and float(row["M_confidence"]) >= TWO_KEY_CONFIDENCE:
                role = "remember"
            else:
                continue
            seen.add(text)
            memory.append((role, label, text, row["line_id"]))
    with (OUT / "memory.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["role", "category", "text", "reviewed_line"])
        w.writerows(memory)
    print(f"config, {len(curated)} library lines, {sum(r == 'remember' for r, *_ in memory)} remembered and {sum(r == 'risk' for r, *_ in memory)} risk lines → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
