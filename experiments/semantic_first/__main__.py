"""Run experiment 6: `uv run python -m experiments.semantic_first` (or `make semantic-first`).

The semantic stage, then the model at temperature 0, against the model alone, on every test set run
so far (PLAN.md). Offline once cache/ holds every embedding and call; a miss needs OPENROUTER_API_KEY.
Writes results/metrics.json and results/lines.csv.
"""

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from evaluation import cord
from evaluation.dev import load_dev, score as score_dev
from evaluation.evaluate import decision_records, receipt_matches, score_items, score_receipts
from evaluation.gold import in_scope, load_gold
from experiments.paraphrase.__main__ import load_variants, reword
from experiments.semantic_first.embed import CANDIDATES, TIE_BREAK, Embedder
from experiments.semantic_first.llm_stage import classify_at
from experiments.semantic_first.semantic import TAXONOMY, Decision, SemanticStage, decontaminate, load_anchors, nearest_anchor_accuracy
from validator.classifier import Classification
from validator.data import ReceiptInput, load_receipts
from validator.llm import LLM, LLMError, ledger_total
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.normaliser import normalise
from validator.pipeline import CONFIDENCE_THRESHOLD, decide_all
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS, CACHE, LEDGER = HERE / "results", HERE / "cache", HERE / "spend.jsonl"
INFORMATIVE_BELOW, ALTERNATIVE_ABOVE = 0.9999, 1e-6  # the temperature-0 gate (PLAN.md)
USEFUL_SHARE, SAFE_ACCURACY = 0.25, 0.98


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying this experiment's cache instead.")
        return LLM.offline(cache_dir=CACHE, ledger_path=LEDGER)


@dataclass
class Arm:
    """One test set under arms B and C."""
    receipts: list[ReceiptInput]
    b: dict[str, Classification]  # receipt ID → the model's answer
    semantic: dict[str, Decision] = field(default_factory=dict)  # line ID → the semantic stage's decision
    c: dict[str, ClassifiedReceipt] = field(default_factory=dict)


def combine(b: Classification, semantic: dict[str, Decision]) -> ClassifiedReceipt:
    """Arm C: the semantic stage's decided lines, and the model's answer for every other line."""
    receipt, items = b.receipt.receipt, {}
    for line in receipt.line_items:
        d = semantic.get(line.line_id)
        if receipt.itemized and d is not None and d.decided:
            items[line.line_id] = ClassifiedItem(line_id=line.line_id, category=d.category, restricted=TAXONOMY[d.category],
                                                 confidence=d.confidence, reason=d.reason)
        else:
            items[line.line_id] = b.receipt.items[line.line_id]
    return ClassifiedReceipt(receipt=receipt, items=items)


def run_set(receipts: list[ReceiptInput], llm: LLM, stage: SemanticStage, embedder: Embedder, model, temperature: float) -> Arm:
    glosses = translate_all(receipts, llm)
    arm = Arm(receipts, {r.receipt_id: classify_at(r, glosses, llm, temperature=temperature) for r in receipts})
    lines = [line for r in receipts if r.itemized for line in r.line_items]
    texts = [normalise(line.description_raw) for line in lines]
    arm.semantic = dict(zip([line.line_id for line in lines], stage.decide(texts, embedder.embed(model, texts))))
    arm.c = {rid: combine(b, arm.semantic) for rid, b in arm.b.items()}
    return arm


def records(classified: list[ClassifiedReceipt]) -> dict[str, dict]:
    _, decisions = decide_all(classified)
    out = decision_records(decisions)
    for c in classified:
        out[c.receipt.receipt_id]["line_items"] = [
            {"line_id": line.line_id, "description_raw": line.description_raw, **c.items[line.line_id].model_dump(exclude={"line_id"})}
            for line in c.receipt.line_items]
    return out


def receipts_score(got: dict[str, dict], gold) -> dict:
    items, receipts = score_items(got, gold), score_receipts(got, gold)
    return {"items": items["accuracy"]["k"], "macro_f1": round(items["macro_f1"], 4), "restricted_f1": round(items["restricted_f1"], 4),
            "exact": receipts["exact_match"]["k"], "false_positives": receipts["disposition"]["false_positives"]["k"],
            "missed": receipts["disposition"]["missed_violations"]["k"],
            "exceptions_found": f"{receipts['exceptions']['matched']} of {receipts['exceptions']['gold']}",
            "misclassified": items["misclassified"],
            "wrong_receipts": [rid for rid in got if in_scope(rid) and not receipt_matches(got[rid], gold[rid])]}


def main() -> int:
    gold, variants = load_gold(), load_variants()
    main_receipts = load_receipts()
    dev_receipts, expected = load_dev()
    reworded = reword(main_receipts, variants)
    cord_receipts, labels = load_receipts(cord.RECEIPTS_PATH), cord.load_labels()

    # 1. Anchors: decontaminate against every test line, then choose the embedding model on the anchors alone.
    test_texts = [line.description_raw for rs in (main_receipts, dev_receipts, reworded, cord_receipts) for r in rs for line in r.line_items]
    anchors, dropped = decontaminate(load_anchors(), test_texts)
    embedder = Embedder()
    loo = {m.name: nearest_anchor_accuracy(embedder.embed(m, [normalise(a.text) for a in anchors]), anchors) for m in CANDIDATES}
    tied = [m for m in CANDIDATES if loo[m.name] == max(loo.values())]
    model = next((m for m in tied if m.name == TIE_BREAK), tied[0])
    stage = SemanticStage(model.name, anchors, embedder.embed(model, [normalise(a.text) for a in anchors]))
    print(f"anchors kept {len(anchors)}, dropped {len(dropped)}; nearest-anchor accuracy {loo}; model {model.name}; "
          f"cutoff {stage.cutoff:.4f}; leave-one-out decided {stage.loo_decided}, right {stage.loo_right}")

    # 2. The temperature gate, on the dev set at temperature 0.
    llm = client()
    dev_glosses = translate_all(dev_receipts, llm)
    dev_t0 = [classify_at(r, dev_glosses, llm, temperature=0.0) for r in dev_receipts]
    dev_items = [(c, line_id, item) for c in dev_t0 for line_id, item in c.receipt.items.items()]
    informative = (any(item.confidence < INFORMATIVE_BELOW for _, _, item in dev_items)
                   and any(a["p"] > ALTERNATIVE_ABOVE for c, line_id, _ in dev_items for a in c.alternatives.get(line_id, [])))
    temperature = 0.0 if informative else 1.0
    print(f"temperature-0 logprobs informative: {informative} → arms B and C run at temperature {temperature}")

    # 3. Every test set, under B and C.
    sets = {name: run_set(rs, llm, stage, embedder, model, temperature)
            for name, rs in (("receipts", main_receipts), ("dev", dev_receipts), ("reworded", reworded), ("cord", cord_receipts))}
    timing = [rid for rid in gold if not in_scope(rid)]

    def arm_classified(name: str, arm: str) -> list[ClassifiedReceipt]:
        s = sets[name]
        return [s.c[r.receipt_id] if arm == "C" else s.b[r.receipt_id].receipt for r in s.receipts]

    results = {}
    for arm in ("B", "C"):
        main_got = records(arm_classified("receipts", arm))
        timing_context = [c for c in arm_classified("receipts", arm) if c.receipt.receipt_id in timing]
        reworded_got = records(arm_classified("reworded", arm) + timing_context)
        dev_rows = score_dev([SimpleNamespace(receipt=c) for c in arm_classified("dev", arm)], expected)
        cord_classified = arm_classified("cord", arm)
        cord_system = {line_id: item.model_dump() for c in cord_classified for line_id, item in c.items.items()}
        cord_want = cord.dispositions(decide_all(cord.labelled(cord_receipts, labels))[1])
        cord_ours = cord.dispositions(decide_all(cord_classified)[1])
        cord_items, cord_receipts_score = cord.score_items(cord_system, labels), cord.score_receipts(cord_ours, cord_want)
        results[arm] = {
            "receipts": receipts_score(main_got, gold),
            "timing": {rid: receipt_matches(main_got[rid], gold[rid]) for rid in sorted(timing)},
            "dev": {"items": len(dev_rows), "category": sum(r.category_ok for r in dev_rows), "item_kind": sum(r.kind_ok for r in dev_rows),
                    "recipient": sum(r.recipient_ok for r in dev_rows),
                    "wrong": [{"line_id": r.line_id, "text": r.text, "got": r.got, "want": r.expected} for r in dev_rows if not r.category_ok]},
            "reworded": receipts_score(reworded_got, gold),
            "cord": {"lines_right": cord_items["all"]["category_correct"]["k"], "lines": cord_items["all"]["items"],
                     "sure_lines_right": cord_items["sure"]["category_correct"]["k"], "sure_lines": cord_items["sure"]["items"],
                     "false_alarms": cord_receipts_score["false_alarms"]["k"], "false_flags": cord_receipts_score["false_flags"]["k"],
                     "false_reviews": cord_receipts_score["false_reviews"]["k"], "clean": cord_receipts_score["false_alarms"]["n"],
                     "missed_flags": cord_receipts_score["missed_flags"]["k"], "violations": cord_receipts_score["missed_flags"]["n"]},
        }

    # 4. Stability (a fresh repeat of the model) and the merchant ablation, on the 32 receipts and the dev set.
    repeat_glosses = translate_all(main_receipts + dev_receipts, llm)
    first = {r.receipt_id: sets[name].b[r.receipt_id] for name in ("receipts", "dev") for r in sets[name].receipts}
    repeat = {r.receipt_id: classify_at(r, repeat_glosses, llm, temperature=temperature, run="repeat") for r in main_receipts + dev_receipts}
    flips = [line_id for rid, c in first.items() for line_id, item in c.receipt.items.items()
             if repeat[rid].receipt.items[line_id].category != item.category]
    drift = max(abs(repeat[rid].receipt.items[line_id].confidence - item.confidence) for rid, c in first.items() for line_id, item in c.receipt.items.items())
    ablation = [classify_at(r, repeat_glosses, llm, temperature=temperature, context=False) for r in main_receipts]
    ablation_right = sum(c.receipt.items[line_id].category == gold[c.receipt.receipt.receipt_id].items[line_id].category
                         for c in ablation if in_scope(c.receipt.receipt.receipt_id) for line_id in c.receipt.items)

    # 5. The semantic stage on its own: every labelled line it decided, and how many lines it took.
    truth = {**{line_id: item.category for g in gold.values() for line_id, item in g.items.items()},
             **{line_id: e["category"] for line_id, e in expected.items()},
             **{line_id: row["category"] for line_id, row in labels.items()}}
    decided = [(name, line_id, d) for name, s in sets.items() for line_id, d in s.semantic.items() if d.decided]
    semantic_wrong = [{"set": name, "line_id": line_id, "got": d.category, "want": truth[line_id]}
                      for name, line_id, d in decided if d.category != truth[line_id]]
    restricted_errors = [w for w in semantic_wrong if TAXONOMY[w["got"]] != TAXONOMY[w["want"]]]
    useful_lines = [line_id for name in ("receipts", "cord") for line_id, d in sets[name].semantic.items()
                    if d.decided and (name == "cord" or in_scope(line_id.rsplit("-", 1)[0]))]
    useful_pool = sum(len(r.line_items) for r in main_receipts if in_scope(r.receipt_id)) + len(labels)
    no_call = {name: sum(all(s.semantic[line.line_id].decided for line in r.line_items) for r in s.receipts if r.itemized)
               for name, s in sets.items()}
    escalation_reasons = {}
    for s in sets.values():
        for d in s.semantic.values():
            key = d.why if not d.why.startswith("margin") else "margin below the cutoff"
            escalation_reasons[key] = escalation_reasons.get(key, 0) + 1

    def passes(r: dict) -> dict:
        return {
            "1 receipts 28/28, 65/65, 0 FP, 0 missed": r["receipts"]["exact"] == 28 and r["receipts"]["items"] == 65
                                                       and r["receipts"]["false_positives"] == 0 and r["receipts"]["missed"] == 0,
            "2 timing 4/4": all(r["timing"].values()),
            "3 dev ≥41 category, ≥42 kind, ≥42 recipient": r["dev"]["category"] >= 41 and r["dev"]["item_kind"] >= 42 and r["dev"]["recipient"] >= 42,
            "4 reworded ≥62 items, ≥26 receipts": r["reworded"]["items"] >= 62 and r["reworded"]["exact"] >= 26,
            "5 CORD ≥248 lines, ≤13 false alarms, 2/2 flagged": r["cord"]["lines_right"] >= 248 and r["cord"]["false_alarms"] <= 13 and r["cord"]["missed_flags"] == 0,
            "6 repeat changes no category": not flips,
        }

    bar = {"B": passes(results["B"]), "C": passes(results["C"])}
    bar["C"]["7 safe: ≥98% right, no restricted error"] = (not decided or (len(decided) - len(semantic_wrong)) / len(decided) >= SAFE_ACCURACY) and not restricted_errors
    bar["C"]["8 useful: ≥25% of the 28's and CORD's lines"] = len(useful_lines) >= USEFUL_SHARE * useful_pool
    metrics = {
        "anchors": {"written": len(load_anchors()), "kept": len(anchors), "dropped": dropped},
        "embedding_models": {"nearest_anchor_accuracy": loo, "chosen": model.name},
        "semantic_stage": {**stage.summary(), "decided": len(decided), "wrong": semantic_wrong, "restricted_errors": restricted_errors,
                           "decided_per_set": {name: f"{sum(d.decided for d in s.semantic.values())} of {len(s.semantic)}" for name, s in sets.items()},
                           "useful": f"{len(useful_lines)} of {useful_pool}", "receipts_needing_no_model_call": no_call,
                           "why_lines_went_to_the_model": escalation_reasons},
        "temperature": {"gate_informative": informative, "used": temperature},
        "arms": results,
        "stability": {"lines_compared": sum(len(c.receipt.items) for c in first.values()), "categories_changed": flips,
                      "largest_confidence_change": round(drift, 6)},
        "merchant_ablation": {"items_right": ablation_right, "items": 65},
        "bar": bar,
        "verdict": {arm: "passes" if all(v.values()) else "fails: " + "; ".join(k for k, ok in v.items() if not ok) for arm, v in bar.items()},
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "spend_usd": ledger_total(LEDGER),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    write_lines(sets, truth, expected)
    print_summary(metrics)
    return 0


def write_lines(sets: dict[str, Arm], truth: dict[str, str], expected: dict[str, dict]) -> None:
    with (RESULTS / "lines.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["set", "line_id", "text", "trap", "want", "semantic_best", "margin", "semantic", "why", "nearest_anchors",
                         "B", "B_confidence", "C", "C_confidence", "B_right", "C_right"])
        for name, s in sets.items():
            for r in s.receipts:
                for line in r.line_items:
                    d, b, c = s.semantic.get(line.line_id), s.b[r.receipt_id].receipt.items[line.line_id], s.c[r.receipt_id].items[line.line_id]
                    want = truth.get(line.line_id, "")
                    writer.writerow([name, line.line_id, line.description_raw, (expected.get(line.line_id) or {}).get("trap") or "", want,
                                     d.category if d else "", f"{d.margin:.4f}" if d else "", "decided" if d and d.decided else "model",
                                     d.why if d else "not itemized", " | ".join(f"{t} ({cat}, {sim:.3f})" for t, cat, sim in d.neighbours) if d else "",
                                     b.category, f"{b.confidence:.4f}", c.category, f"{c.confidence:.4f}", b.category == want, c.category == want])


def print_summary(m: dict) -> None:
    s = m["semantic_stage"]
    print(f"semantic stage ({s['model']}): decided {s['decided']} lines; per set {s['decided_per_set']}; useful {s['useful']}; "
          f"wrong {len(s['wrong'])}, restricted errors {len(s['restricted_errors'])}")
    print(f"receipts needing no model call: {s['receipts_needing_no_model_call']}")
    for arm, r in m["arms"].items():
        print(f"{arm}: 28 receipts {r['receipts']['exact']}/28 exact, items {r['receipts']['items']}/65, FP {r['receipts']['false_positives']}, "
              f"missed {r['receipts']['missed']}; timing {sum(r['timing'].values())}/4; dev {r['dev']['category']}/43 "
              f"(kind {r['dev']['item_kind']}, recipient {r['dev']['recipient']}); reworded {r['reworded']['items']}/65 items, "
              f"{r['reworded']['exact']}/28 receipts; CORD {r['cord']['lines_right']}/270 lines, false alarms {r['cord']['false_alarms']}/98, "
              f"missed {r['cord']['missed_flags']}/2")
    print(f"stability: {len(m['stability']['categories_changed'])} categories changed; ablation {m['merchant_ablation']['items_right']}/65")
    print(f"verdict: {m['verdict']}; spend ${m['spend_usd']:.4f}")


if __name__ == "__main__":
    sys.exit(main())
