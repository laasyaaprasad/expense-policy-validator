"""English glosses for non-English line items: one cached call per receipt that has any.

The gloss sits beside the original text; it never replaces it. The classifier sees both, and the
output keeps `description_raw` exactly as printed. English lines make no call and get no gloss.
Translation runs at temperature 0: it needs no confidence, and greedy output is the most stable.

`python -m validator.translate` translates every receipt (from the cache when it can) and writes
review/translations.csv for the hand check, keeping any `ok` marks whose gloss is unchanged.
"""

import csv
import json
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from validator.data import ROOT, ReceiptInput, load_receipts
from validator.llm import LLM, complete_json
from validator.normaliser import normalise

PROMPT_VERSION = "translate-v1"
REVIEW_PATH = ROOT / "review" / "translations.csv"
SYSTEM = (
    "You translate line items from expense receipts into English. Translate each item literally and "
    "briefly, as it would appear on an English receipt. Keep brand names, sizes and quantities. Do not "
    "classify, explain or add anything. Reply in json only, in this format: "
    '{"items": [{"line_id": "...", "english": "..."}]}'
)


class Gloss(BaseModel):
    line_id: str
    english: str = Field(min_length=1)


class Glosses(BaseModel):
    items: list[Gloss]


def translate_receipt(receipt: ReceiptInput, llm: LLM, trace: dict | None = None) -> dict[str, str]:
    """Line ID → English gloss, for the receipt's non-English lines only. If `trace` is given, the
    call behind the glosses is recorded in it under the receipt ID."""
    foreign = [line for line in receipt.line_items if line.language != "en"]
    if not foreign:
        return {}
    wanted = [line.line_id for line in foreign]
    payload = {"items": [{"line_id": line.line_id, "language": line.language, "text": normalise(line.description_raw)}
                         for line in foreign]}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

    def check(parsed: Glosses) -> list[str]:
        returned = [gloss.line_id for gloss in parsed.items if gloss.english.strip()]
        return [f"missing or empty line_id {line_id}" for line_id in wanted if line_id not in returned]

    parsed, completion = complete_json(llm, messages, Glosses, purpose="translate", prompt_version=PROMPT_VERSION,
                                       check=check, max_tokens=1000)
    if trace is not None:
        trace[receipt.receipt_id] = {"call": completion.cache_key[:24], "cached": completion.cached,
                                     "retried_after": list(completion.retried_after)}
    glosses = {gloss.line_id: gloss.english.strip() for gloss in parsed.items}
    return {line_id: glosses[line_id] for line_id in wanted}


def translate_all(receipts: list[ReceiptInput], llm: LLM, trace: dict | None = None) -> dict[str, str]:
    return {line_id: gloss for receipt in receipts for line_id, gloss in translate_receipt(receipt, llm, trace).items()}


def write_review(receipts: list[ReceiptInput], glosses: dict[str, str], path: Path = REVIEW_PATH) -> None:
    previous = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            previous = {row["line_id"]: row for row in csv.DictReader(f)}
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["line_id", "language", "original", "english", "ok"])
        writer.writeheader()
        for receipt in receipts:
            for line in receipt.line_items:
                if line.line_id not in glosses:
                    continue
                old = previous.get(line.line_id, {})
                ok = old.get("ok", "") if old.get("english") == glosses[line.line_id] else ""
                writer.writerow({"line_id": line.line_id, "language": line.language, "original": line.description_raw,
                                 "english": glosses[line.line_id], "ok": ok})


def main() -> int:
    receipts = load_receipts()
    glosses = translate_all(receipts, LLM())
    write_review(receipts, glosses)
    print(f"{len(glosses)} glosses; wrote {REVIEW_PATH.relative_to(ROOT)}. Fill in the ok column after checking each one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
