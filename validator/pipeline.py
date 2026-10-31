"""The pipeline: normalise → translate → classify → retrieve → engine → report, for all 32 receipts.

Every stage reads only the receipt and the stages before it. All 32 receipts go through, because the
engine's cross-receipt totals need the out-of-scope ones too.

`make run` writes outputs/: classifications.jsonl, retrieval.jsonl and results.jsonl. With
the committed cache it makes no calls; without a key in .env it replays the cache offline, where a
miss is an error.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from validator.classifier import Classification, classify
from validator.data import ROOT, ReceiptInput, load_receipts
from validator.engine import evaluate
from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.llm import LLM, LLMError
from validator.models import ClassifiedReceipt, Decision
from validator.policies import load_policies
from validator.reporter import ReceiptResult, report, to_json_line
from validator.retriever import Retrieval, retrieve
from validator.rules_config import load_rules, policy_version
from validator.translate import translate_all

OUTPUT_DIR = ROOT / "outputs"


@dataclass(frozen=True)
class Run:
    glosses: dict[str, str]
    classifications: list[Classification]
    retrievals: dict[str, Retrieval]
    decisions: list[Decision]
    results: list[ReceiptResult]


def classify_all(receipts: list[ReceiptInput], llm: LLM, *, context: bool = True, run: str = "") -> tuple[dict[str, str], list[Classification]]:
    glosses = translate_all(receipts, llm)
    return glosses, [classify(r, glosses, llm, context=context, run=run) for r in receipts]


def decide_all(classified: list[ClassifiedReceipt], threshold: float = CONFIDENCE_THRESHOLD) -> tuple[dict[str, Retrieval], list[Decision]]:
    """Retrieve, then decide. Shared with the blame report, which reruns it on corrected inputs."""
    config, policies = load_rules(), load_policies()
    retrievals = {c.receipt.receipt_id: retrieve(c, config, policies) for c in classified}
    decisions = evaluate(classified, config, {receipt_id: r.rule_ids for receipt_id, r in retrievals.items()}, threshold)
    return retrievals, decisions


def run(llm: LLM, receipts: list[ReceiptInput] | None = None) -> Run:
    receipts = receipts if receipts is not None else load_receipts()
    glosses, classifications = classify_all(receipts, llm)
    by_id = {c.receipt.receipt.receipt_id: c for c in classifications}
    retrievals, decisions = decide_all([c.receipt for c in classifications])
    config, version = load_rules(), policy_version()
    results = [
        report(by_id[d.receipt_id].receipt, d, config, version, provider=llm.settings.provider, model=llm.settings.model,
               prompt_version=by_id[d.receipt_id].prompt_version, confidence_threshold=CONFIDENCE_THRESHOLD)
        for d in decisions
    ]
    return Run(glosses, classifications, retrievals, decisions, results)


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


def write_run(result: Run, out_dir: Path = OUTPUT_DIR) -> None:
    write_jsonl(classification_rows(result.glosses, result.classifications), out_dir / "classifications.jsonl")
    write_jsonl([asdict(r) for r in result.retrievals.values()], out_dir / "retrieval.jsonl")
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in result.results:
            f.write(to_json_line(r) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="replay the committed cache; any miss is an error")
    args = parser.parse_args(argv)
    if args.offline:
        llm = LLM.offline()
    else:
        try:
            llm = LLM()
        except LLMError as e:
            print(f"{e}. Replaying the committed cache instead.")
            llm = LLM.offline()
    result = run(llm)
    write_run(result)
    dispositions = [r.disposition for r in result.results]
    print(f"{len(result.results)} receipts → {(OUTPUT_DIR / 'results.jsonl').relative_to(ROOT)}: "
          + ", ".join(f"{dispositions.count(d)} {d}" for d in ("flag", "review", "pass")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
