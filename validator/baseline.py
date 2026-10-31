"""Keyword baseline: a restricted-word filter, the kind of system this project replaces.

It swaps the classifier only. Its labels go through the same retriever, engine and reporter, so any
difference from the pipeline's results is the classifier's. It never calls a model and never reads
the translation glosses. Every line gets confidence 1.0, so it never raises EXC-03, and it extracts
no recipients or item kinds. Non-itemized lines are `other`, as in the pipeline.

The word list is rules/keywords.yaml; its header says how matching works.
`make baseline` writes outputs/baseline.jsonl.
"""

import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from validator.data import ROOT, ReceiptInput, load_receipts
from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.reporter import ReceiptResult, report, to_json_line
from validator.rules_config import load_rules, policy_version
from validator.taxonomy import load_taxonomy

KEYWORDS_PATH = ROOT / "rules" / "keywords.yaml"
BASELINE_PATH = ROOT / "outputs" / "baseline.jsonl"
TAXONOMY = load_taxonomy()


def fold(text: str) -> str:
    """NFKC, casefold, then drop accents (combining marks), applied to both the text and the terms.
    Japanese voicing marks are combining marks too; dropping them on both sides keeps matches intact."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFKC", text)


def _is_japanese(term: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in term)


@dataclass(frozen=True)
class Term:
    category: str
    text: str  # folded
    pattern: re.Pattern

    @property
    def restricted(self) -> bool:
        return TAXONOMY[self.category]


def _term(category: str, raw: str) -> Term:
    text = fold(raw)
    if _is_japanese(text):
        pattern = re.compile(re.escape(text))
    else:
        words = r"\s+".join(re.escape(word) for word in text.split())
        pattern = re.compile(rf"(?<!\w){words}(?:s|es)?(?!\w)")
    return Term(category, text, pattern)


@cache
def load_terms(path: Path = KEYWORDS_PATH) -> tuple[Term, ...]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    terms = [_term(category, raw) for category, by_language in spec["restricted"].items()
             for words in by_language.values() for raw in words]
    terms += [_term(category, raw) for category, words in spec["unrestricted"].items() for raw in words]
    unknown = {t.category for t in terms} - set(TAXONOMY)
    if unknown:
        raise ValueError(f"keywords.yaml names categories not in the taxonomy: {sorted(unknown)}")
    return tuple(terms)


def match(text: str, terms: tuple[Term, ...] | None = None) -> Term | None:
    """Any restricted match beats any unrestricted one; then the longest term; then taxonomy order."""
    order = list(TAXONOMY)
    folded = fold(text)
    hits = [t for t in (terms or load_terms()) if t.pattern.search(folded)]
    return min(hits, key=lambda t: (not t.restricted, -len(t.text), order.index(t.category)), default=None)


def classify_receipt(receipt: ReceiptInput) -> ClassifiedReceipt:
    items = {}
    for line in receipt.line_items:
        hit = match(line.description_raw) if receipt.itemized else None
        category = hit.category if hit else "other"
        reason = (f'keyword "{hit.text}"' if hit else
                  "no keyword matched" if receipt.itemized else "not itemized")
        items[line.line_id] = ClassifiedItem(line_id=line.line_id, category=category, restricted=TAXONOMY[category],
                                             confidence=1.0, reason=reason)
    return ClassifiedReceipt(receipt=receipt, items=items)


def keywords_version(path: Path = KEYWORDS_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def run_baseline(receipts: list[ReceiptInput] | None = None) -> list[ReceiptResult]:
    from validator.pipeline import decide_all  # the pipeline's own retrieval and engine

    classified = [classify_receipt(r) for r in (receipts if receipts is not None else load_receipts())]
    by_id = {c.receipt.receipt_id: c for c in classified}
    _, decisions = decide_all(classified)
    config, version = load_rules(), policy_version()
    return [report(by_id[d.receipt_id], d, config, version, provider="none", model="keyword-baseline",
                   prompt_version=f"keywords-{keywords_version()}", confidence_threshold=CONFIDENCE_THRESHOLD)
            for d in decisions]


def main() -> int:
    results = run_baseline()
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BASELINE_PATH.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(to_json_line(r) + "\n")
    dispositions = [r.disposition for r in results]
    print(f"{len(results)} receipts → {BASELINE_PATH.relative_to(ROOT)}: "
          + ", ".join(f"{dispositions.count(d)} {d}" for d in ("flag", "review", "pass")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
