"""Run the verifier experiment: `uv run python -m experiments.verifier` (or `make verifier`).

Asks the decision model (PLAN.md: Mammoth-1) one yes/no question about every line the submitted
classifier calls restricted, then reruns the unchanged engine under the two variants in PLAN.md.
Offline once cache/ holds every answer; a missing answer needs AI_GATEWAY_API_KEY (Vercel AI
Gateway). Writes results/.
"""

import csv
import json
import sys
from pathlib import Path

from sklearn.metrics import roc_auc_score

from evaluation import cord
from evaluation.blame import load_classified
from evaluation.dev import load_dev
from evaluation.evaluate import decision_records, receipt_matches, score_receipts
from evaluation.gold import in_scope, load_gold
from experiments.verifier.decisions import MODEL, Decisions, probability
from experiments.verifier.questions import QUESTION_ID, input_text, predicate
from validator.classifier import TAXONOMY
from validator.data import ROOT, load_receipts, read_jsonl
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.pipeline import decide_all

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
VETO_BELOW = 0.1  # PLAN.md, variant 2; fixed in advance, not tuned


def alternatives(path: Path) -> dict[str, list[dict]]:
    """Line ID -> the classifier's next most likely categories, from the committed trace."""
    return {line["line_id"]: line.get("alternatives", []) for t in read_jsonl(path) for line in t["classify"]["lines"]}


def apply(classified: list[ClassifiedReceipt], p_yes: dict[str, float], alts: dict[str, list[dict]], variant: str) -> list[ClassifiedReceipt]:
    out = []
    for c in classified:
        items = {}
        for line_id, item in c.items.items():
            if line_id in p_yes and variant == "doubt":
                item = item.model_copy(update={"confidence": min(item.confidence, p_yes[line_id])})
            elif line_id in p_yes and variant == "veto" and p_yes[line_id] < VETO_BELOW:
                runner_up = next((a["category"] for a in alts.get(line_id, []) if not TAXONOMY[a["category"]]), "other")
                item = item.model_copy(update={"category": runner_up, "restricted": False, "confidence": 1 - p_yes[line_id],
                                               "item_kind": None, "reason": f"verifier vetoed {item.category} (yes {p_yes[line_id]:.3f})"})
            items[line_id] = item
        out.append(c.model_copy(update={"items": items}))
    return out


REFUSED: dict[str, list[str]] = {}  # set -> lines the model declined to answer; they keep the classifier's answer


def ask_all(client: Decisions, classified: list[ClassifiedReceipt], glosses: dict[str, str | None], purpose: str) -> dict[str, float]:
    p_yes = {}
    for c in classified:
        lines = {line.line_id: line for line in c.receipt.line_items}
        for line_id, item in c.items.items():
            if item.restricted and c.receipt.itemized:
                response = client.ask(input_text(c.receipt, lines[line_id].description_raw, glosses.get(line_id)),
                                      [predicate(item.category)], purpose=purpose)
                p = probability(response, QUESTION_ID)
                if p is None:
                    REFUSED.setdefault(purpose, []).append(line_id)
                else:
                    p_yes[line_id] = p
    return p_yes


def separation(p_yes: dict[str, float], truly: dict[str, bool]) -> dict:
    ids = [i for i in p_yes if i in truly]
    real = [p_yes[i] for i in ids if truly[i]]
    false = [p_yes[i] for i in ids if not truly[i]]
    auroc = roc_auc_score([truly[i] for i in ids], [p_yes[i] for i in ids]) if real and false else None
    return {"checked": len(ids), "truly_restricted": len(real), "falsely_restricted": len(false),
            "real_below_0.5": sum(p < 0.5 for p in real), "false_below_0.5": sum(p < 0.5 for p in false),
            "real_below_0.1": sum(p < VETO_BELOW for p in real), "false_below_0.1": sum(p < VETO_BELOW for p in false),
            "auroc": auroc}


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    client = Decisions()
    variants = ("llm_only", "doubt", "veto")

    receipts = load_classified()
    gold = load_gold()
    receipt_rows = {r["line_id"]: r for r in read_jsonl(ROOT / "outputs" / "classifications.jsonl")}
    receipt_p = ask_all(client, receipts, {i: r.get("english") for i, r in receipt_rows.items()}, "receipts")
    receipt_alts = alternatives(ROOT / "outputs" / "traces.jsonl")
    receipt_truth = {i: item.restricted for rid, g in gold.items() if in_scope(rid) for i, item in g.items.items()}

    cord_receipts = load_receipts(cord.RECEIPTS_PATH)
    cord_classified, cord_rows = cord.load_system(cord_receipts)
    cord_p = ask_all(client, cord_classified, {i: r.get("english") for i, r in cord_rows.items()}, "cord")
    cord_alts = alternatives(cord.OUT_DIR / "traces.jsonl")
    cord_labels = cord.load_labels()
    cord_truth = {i: bool(l["restricted"]) for i, l in cord_labels.items()}
    cord_want = cord.dispositions(decide_all(cord.labelled(cord_receipts, cord_labels))[1])

    dev_receipts, expected = load_dev()
    dev_rows = {r["line_id"]: r for r in read_jsonl(ROOT / "outputs" / "dev" / "configured.jsonl")}
    dev_classified = [ClassifiedReceipt(receipt=r, items={
        line.line_id: ClassifiedItem(line_id=line.line_id, category=dev_rows[line.line_id]["got"],
                                     restricted=TAXONOMY[dev_rows[line.line_id]["got"]], confidence=dev_rows[line.line_id]["confidence"])
        for line in r.line_items}) for r in dev_receipts]
    dev_p = ask_all(client, dev_classified, {}, "dev")  # no glosses were saved for the dev run
    dev_truth = {i: TAXONOMY[e["category"]] for i, e in expected.items()}

    results = {"receipts": {}, "cord": {}, "dev": {}}
    for variant in variants:
        mixed = receipts if variant == "llm_only" else apply(receipts, receipt_p, receipt_alts, variant)
        records = decision_records(decide_all(mixed)[1])
        scored = score_receipts(records, gold)
        results["receipts"][variant] = {
            "exact_match": scored["exact_match"], "false_positives": scored["disposition"]["false_positives"],
            "missed_violations": scored["disposition"]["missed_violations"],
            "wrong_receipts": sorted(rid for rid in records if in_scope(rid) and not receipt_matches(records[rid], gold[rid])),
        }
        mixed = cord_classified if variant == "llm_only" else apply(cord_classified, cord_p, cord_alts, variant)
        decided = cord.score_receipts(cord.dispositions(decide_all(mixed)[1]), cord_want)
        results["cord"][variant] = {k: decided[k] for k in ("false_flags", "false_reviews", "false_alarms", "flag_precision", "missed_flags")}
        mixed = dev_classified if variant == "llm_only" else apply(dev_classified, dev_p, {}, variant)
        got = {i: item for c in mixed for i, item in c.items.items()}
        results["dev"][variant] = {"restricted_errors": sum(got[i].restricted != dev_truth[i] for i in dev_truth)}

    spend = [json.loads(row) for row in (HERE / "spend.jsonl").read_text(encoding="utf-8").splitlines() if row.strip()]
    calls = [e for e in spend if e["purpose"] in ("receipts", "cord", "dev")]
    metrics = {
        "note": "Post-freeze experiment (PLAN.md). The decision model answers one yes/no predicate per restricted line; the engine is unchanged.",
        "model": MODEL, "veto_below": VETO_BELOW,
        "separation": {"receipts": separation(receipt_p, receipt_truth), "cord": separation(cord_p, cord_truth),
                       "dev": separation(dev_p, dev_truth)},
        "sets": results,
        "spend": {"calls": len(calls), "usd": sum(e["usd"] or 0 for e in calls), "refused": REFUSED,
                  "mean_seconds": sum(e["seconds"] for e in calls) / len(calls) if calls else None},
    }
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")

    with (RESULTS / "lines.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["set", "line_id", "text", "category", "deepseek_confidence", "verifier_yes", "truly_restricted"])
        sets = (("receipts", receipts, receipt_p, None), ("cord", cord_classified, cord_p, cord_truth), ("dev", dev_classified, dev_p, dev_truth))
        for name, classified, p_yes, truth in sets:
            for c in classified:
                text = {line.line_id: line.description_raw for line in c.receipt.line_items}
                for i, item in c.items.items():
                    if i in p_yes:
                        # The receipts' answer key is the organisers' and is not redistributed (.gitignore).
                        writer.writerow([name, i, text[i], item.category, item.confidence, f"{p_yes[i]:.4f}",
                                         "" if truth is None else truth.get(i, "")])
    print(json.dumps(metrics, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
