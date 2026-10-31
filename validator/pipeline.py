"""The pipeline: normalise → translate → classify → retrieve → engine → report, for all 32 receipts.

Every stage after translation reads only the receipt and the stage before it. All 32 receipts go
through, because the engine's cross-receipt totals need the out-of-scope ones too.

`python -m validator.pipeline` writes outputs/<VERSION>/. With a cold cache it needs a key; with the
committed cache, `--offline` reruns it with no key and no calls.
"""

import argparse
import json
import sys
from pathlib import Path

from validator.classifier import Classification, classify
from validator.data import ROOT, ReceiptInput, load_receipts
from validator.llm import LLM
from validator.translate import translate_all

VERSION = "v1"  # bumped by every change made after seeing a gold error (NOTES.md)
OUTPUT_DIR = ROOT / "outputs" / VERSION


def classify_all(receipts: list[ReceiptInput], llm: LLM, *, context: bool = True, run: str = "") -> tuple[dict[str, str], list[Classification]]:
    glosses = translate_all(receipts, llm)
    return glosses, [classify(r, glosses, llm, context=context, run=run) for r in receipts]


def classification_rows(glosses: dict[str, str], classifications: list[Classification]) -> list[dict]:
    """One row per line item: the text the classifier read and everything it returned."""
    rows = []
    for c in classifications:
        for line in c.receipt.receipt.line_items:
            item = c.receipt.items[line.line_id]
            rows.append({
                "receipt_id": c.receipt.receipt.receipt_id,
                "line_id": line.line_id,
                "description_raw": line.description_raw,
                "language": line.language,
                "english": glosses.get(line.line_id),
                **item.model_dump(exclude={"line_id"}),
                "prompt_version": c.prompt_version,
                "model": c.model,
                "cache_key": c.cache_key,
            })
    return rows


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="replay the committed cache; any miss is an error")
    args = parser.parse_args(argv)
    llm = LLM.offline() if args.offline else LLM()
    glosses, classifications = classify_all(load_receipts(), llm)
    rows = classification_rows(glosses, classifications)
    write_jsonl(rows, OUTPUT_DIR / "classifications.jsonl")
    print(f"{len(rows)} items on {len(classifications)} receipts → {(OUTPUT_DIR / 'classifications.jsonl').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
