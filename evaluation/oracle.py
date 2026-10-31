"""Oracle mode: the engine on the correct categories, with every rule enabled.

This isolates the engine from the classifier and the retriever. With perfect inputs, any mismatch
with the answer key is rule logic and nothing else. It is evaluation, not the pipeline: its output
never feeds the submitted results.

Run with `make oracle`. Exits non-zero if any in-scope receipt differs from the answer key.
"""

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

from evaluation.gold import GoldReceipt, in_scope, load_gold
from validator.data import ROOT, ReceiptInput, load_receipts
from validator.engine import evaluate
from validator.models import ClassifiedItem, ClassifiedReceipt, Decision
from validator.reporter import ReceiptResult, report, to_json_line
from validator.rules_config import load_rules, policy_version

ATTRIBUTES_PATH = ROOT / "review" / "item_attributes.csv"
ORACLE_PATH = ROOT / "outputs" / "oracle.jsonl"


def load_attributes(path: Path = ATTRIBUTES_PATH) -> dict[str, dict]:
    """Line ID → the recipient and item kind read by hand from the line text."""
    with path.open(encoding="utf-8", newline="") as f:
        return {
            row["line_id"]: {key: row[key] or None for key in ("recipient_name", "recipient_type", "item_kind")}
            for row in csv.DictReader(f)
        }


def oracle_receipts(receipts: list[ReceiptInput], gold: dict[str, GoldReceipt], attributes: dict[str, dict]) -> list[ClassifiedReceipt]:
    classified = []
    for receipt in receipts:
        labels = gold[receipt.receipt_id].items
        items = {
            line.line_id: ClassifiedItem(
                line_id=line.line_id,
                category=labels[line.line_id].category,
                restricted=labels[line.line_id].restricted,
                confidence=1.0,
                **attributes.get(line.line_id, {}),
            )
            for line in receipt.line_items
        }
        classified.append(ClassifiedReceipt(receipt=receipt, items=items))
    return classified


def run_oracle() -> list[Decision]:
    """All 32 receipts go in, so the cross-receipt totals are complete; 28 are scored."""
    return evaluate(oracle_receipts(load_receipts(), load_gold(), load_attributes()))


def oracle_results() -> list[ReceiptResult]:
    """The oracle decisions as output records, in the same shape as the pipeline's results."""
    classified = {c.receipt.receipt_id: c for c in oracle_receipts(load_receipts(), load_gold(), load_attributes())}
    config, version = load_rules(), policy_version()
    return [report(classified[d.receipt_id], d, config, version) for d in evaluate(list(classified.values()), config)]


@dataclass
class Comparison:
    receipt_id: str
    disposition: tuple[str, str]  # (ours, gold)
    missing: list[str] = field(default_factory=list)  # gold exceptions we did not raise
    extra: list[str] = field(default_factory=list)  # exceptions we raised that gold does not have
    wrong_lines: dict[str, tuple] = field(default_factory=dict)  # rule → (our line_ids, gold line_ids)
    missing_signals: list[str] = field(default_factory=list)
    extra_signals: list[str] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        return (self.disposition[0] == self.disposition[1] and not self.missing and not self.extra
                and not self.wrong_lines and not self.missing_signals and not self.extra_signals)


def compare(decision: Decision, gold: GoldReceipt) -> Comparison:
    """The data dictionary's matching: exceptions on receipt + rule_id, evidence on equal line_ids,
    signals on receipt + rule_id."""
    ours = {finding.rule_id: tuple(sorted(finding.line_ids)) for finding in decision.exceptions}
    theirs = {exception.rule_id: tuple(sorted(exception.line_ids)) for exception in gold.exceptions}
    our_signals = {signal.rule_id for signal in decision.review_signals}
    their_signals = {signal.rule_id for signal in gold.review_signals}
    return Comparison(
        receipt_id=decision.receipt_id,
        disposition=(decision.disposition, gold.disposition),
        missing=sorted(theirs.keys() - ours.keys()),
        extra=sorted(ours.keys() - theirs.keys()),
        wrong_lines={rule: (ours[rule], theirs[rule]) for rule in sorted(ours.keys() & theirs.keys()) if ours[rule] != theirs[rule]},
        missing_signals=sorted(their_signals - our_signals),
        extra_signals=sorted(our_signals - their_signals),
    )


def write_jsonl(results: list[ReceiptResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(to_json_line(result) + "\n")


def main() -> int:
    decisions = run_oracle()
    write_jsonl(oracle_results(), ORACLE_PATH)
    gold = load_gold()
    comparisons = [compare(d, gold[d.receipt_id]) for d in decisions if in_scope(d.receipt_id)]
    for c in comparisons:
        status = "ok " if c.matches else "DIFF"
        line = f"{status} {c.receipt_id}  {c.disposition[0]:6} (gold {c.disposition[1]})"
        problems = [
            *(f"missing {rule}" for rule in c.missing),
            *(f"extra {rule}" for rule in c.extra),
            *(f"{rule} lines {ours} vs gold {theirs}" for rule, (ours, theirs) in c.wrong_lines.items()),
            *(f"missing signal {rule}" for rule in c.missing_signals),
            *(f"extra signal {rule}" for rule in c.extra_signals),
        ]
        print(line + ("  " + "; ".join(problems) if problems else ""))
    matched = sum(c.matches for c in comparisons)
    print(f"\n{matched} / {len(comparisons)} in-scope receipts match the answer key. Wrote {ORACLE_PATH.relative_to(ROOT)}.")
    return 0 if matched == len(comparisons) else 1


if __name__ == "__main__":
    sys.exit(main())
