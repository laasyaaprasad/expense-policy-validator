"""Run experiment 11: `make semantic-v33` (uv run --group semantic python -m experiments.semantic_v33).

Version 3.3 of the lookup against the model on every line (M), V3 and V3.2, on the look-alike trap set (with the
committed memory and with the memory learned by the replay), on a replay of experiment 10's stream (CORD training
R400–R599 warm-up, R600–R799 scored), and on the project's own sets. The plan and the bar are in PLAN.md.
"""

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from evaluation import cord
from evaluation.dev import load_dev, score as score_dev
from evaluation.evaluate import receipt_matches
from evaluation.gold import in_scope, load_gold
from experiments.paraphrase.__main__ import load_variants, reword
from experiments.semantic_first.__main__ import receipts_score, records
from experiments.semantic_v2.__main__ import cord_score
from experiments.semantic_v31.__main__ import OVERLAP, lookup, lookup_stats, stream
from validator.classifier import classify
from validator.data import ROOT, load_receipts
from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.llm import CACHE_DIR, LLM, LLMError, ledger_total
from validator.lookup import TAXONOMY, Embedder, Lookup
from validator.normaliser import normalise
from validator.pipeline import decide_all
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS, LEDGER = HERE / "results", HERE / "spend.jsonl"
V32_CACHE = ROOT / "experiments" / "semantic_v32" / "cache" / "vectors.npz"

V32 = {"memory_rule": "same_item", "tau_same": 0.665334, "word_overlap": OVERLAP}  # experiment 10, as run
V33 = {**V32, "block_tau": 0.854605, "block_same_item": True, "restricted_words": True, "library_agrees": True}
ARMS = {"V3": {}, "V3.2": V32, "V3.3": V33,
        "V3.3 without the word list": {**V33, "restricted_words": False},
        "V3.3 without the second vote": {**V33, "library_agrees": False}}


class LayeredEmbedder(Embedder):
    """Reads experiment 10's committed vectors too, and saves only the new ones here."""

    def __init__(self, model: dict):
        super().__init__(model, HERE / "cache" / "vectors.npz")
        self.base = Embedder(model, V32_CACHE)._cache
        self._cache = {**self.base, **self._cache}

    def save(self) -> None:
        own = {k: v for k, v in self._cache.items() if k not in self.base}
        if own:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            keys = sorted(own)
            np.savez_compressed(self.cache_path, keys=np.array(keys), vectors=np.stack([own[k] for k in keys]))


def client() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER)
    except LLMError as e:
        print(f"{e}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER)


def traps(name: str, memory: str, lk: Lookup | None, receipts, labels, glosses, llm) -> tuple[dict, list[dict]]:
    """The lookup on the trap set, without learning. A miss: a restricted line ending in a safe category, sure enough
    (confidence ≥ the EXC-03 threshold) not to go to a person."""
    rows, classified = [], []
    for r in receipts:
        c = classify(r, glosses, llm, lookup=lk)
        classified.append(c.receipt)
        for line in r.line_items:
            who, item, label = c.decided_by.get(line.line_id, {}), c.receipt.items[line.line_id], labels[line.line_id]
            rows.append({"arm": name, "memory": memory, "line_id": line.line_id, "text": line.description_raw, "label": label["category"],
                         "restricted": label["restricted"], "decided_by": who.get("decided_by", "model"), "why": who.get("why", ""),
                         "match": who["nearest"][0][0] if who.get("nearest") else "",
                         "nearest_restricted": who["nearest"][1][0] if len(who.get("nearest", [])) > 1 else "",
                         "category": item.category, "confidence": round(item.confidence, 4)})
    restricted = [x for x in rows if x["restricted"]]
    safe = [x for x in rows if not x["restricted"]]
    decided = lambda xs: [x for x in xs if x["decided_by"].startswith("lookup")]
    stats = {
        "restricted_decided_safe": [f'{x["text"]} → {x["category"]} (via "{x["match"]}")' for x in decided(restricted)],
        "restricted_sent_to_model_by_word_list": sum(x["why"].startswith("a restricted word") for x in restricted),
        "restricted_missed": [f'{x["text"]} → {x["category"]} ({x["decided_by"]})' for x in restricted
                              if not TAXONOMY[x["category"]] and x["confidence"] >= CONFIDENCE_THRESHOLD],
        "restricted_to_human": sum(not TAXONOMY[x["category"]] and x["confidence"] < CONFIDENCE_THRESHOLD for x in restricted),
        "safe_decided": len(decided(safe)), "safe_decided_right": sum(x["category"] == x["label"] for x in decided(safe)),
        "safe_flagged": [f'{x["text"]} → {x["category"]}' for x in safe if TAXONOMY[x["category"]] and x["confidence"] >= CONFIDENCE_THRESHOLD],
        "score": trap_score(classified, receipts, labels, rows),
    }
    return stats, rows


def trap_score(classified, receipts, labels, rows) -> dict:
    """cord_score without its unsure-label block: every trap label is sure, and sklearn refuses an empty block."""
    ours = cord.dispositions(decide_all(classified)[1])
    want = cord.dispositions(decide_all(cord.labelled(receipts, labels))[1])
    r = cord.score_receipts(ours, want)
    return {"lines_right": sum(x["category"] == x["label"] for x in rows), "lines": len(rows),
            "false_alarms": r["false_alarms"]["k"], "clean": r["false_alarms"]["n"],
            "missed": r["missed_flags"]["k"], "violations": r["missed_flags"]["n"]}


def own_sets(lk: Lookup | None, llm) -> dict:
    gold, variants = load_gold(), load_variants()
    main_receipts = load_receipts()
    dev_receipts, expected = load_dev()
    reworded = reword(main_receipts, variants)
    timing = sorted(rid for rid in gold if not in_scope(rid))
    g = translate_all(main_receipts + dev_receipts + reworded, llm)
    got = records([classify(r, g, llm, lookup=lk).receipt for r in main_receipts])
    dev_rows = score_dev([SimpleNamespace(receipt=classify(r, g, llm, lookup=lk).receipt) for r in dev_receipts], expected)
    timing_context = [classify(r, g, llm, lookup=lk).receipt for r in main_receipts if r.receipt_id in timing]
    rw = records([classify(r, g, llm, lookup=lk).receipt for r in reworded] + timing_context)
    out = {"receipts": receipts_score(got, gold), "timing": {rid: receipt_matches(got[rid], gold[rid]) for rid in timing},
           "dev": {"category": sum(r.category_ok for r in dev_rows), "item_kind": sum(r.kind_ok for r in dev_rows), "recipient": sum(r.recipient_ok for r in dev_rows)},
           "reworded": receipts_score(rw, gold)}
    if lk is not None:
        out["lines_decided_on_32"] = sum(d.decided for r in main_receipts for d in lk.decide_receipt(r).values())
    return out


def main() -> int:
    split = lambda d: (load_receipts(ROOT / d / "receipts.jsonl"), cord.load_labels(ROOT / d / "labels.jsonl"))
    trap_receipts, trap_labels = split("experiments/semantic_v33/traps")
    warm, warm_labels = split("cord/train2")
    fresh, labels = split("cord/train3")

    emb = LayeredEmbedder(Lookup().config["model"])
    llm = client()
    trap_glosses, warm_glosses, glosses = translate_all(trap_receipts, llm), translate_all(warm, llm), translate_all(fresh, llm)
    m_warm = {r.receipt_id: classify(r, warm_glosses, llm) for r in warm}
    m = {r.receipt_id: classify(r, glosses, llm) for r in fresh}
    m_score = cord_score([m[r.receipt_id].receipt for r in fresh], fresh, labels)
    m_traps, trap_rows = traps("M", "none", None, trap_receipts, trap_labels, trap_glosses, llm)

    arms, rows = {}, []
    for name, overrides in ARMS.items():
        lk = lookup(overrides, emb)
        before, r1 = traps(name, "committed", lk, trap_receipts, trap_labels, trap_glosses, llm)
        warmed = {}
        for r in warm:  # review R400–R599 first: not scored
            for line in r.line_items:
                mi, lab = m_warm[r.receipt_id].receipt.items[line.line_id], warm_labels[line.line_id]
                out = lk.learn(line.description_raw, lab["category"], lab["sure"], mi.category, mi.confidence)
                warmed[out] = warmed.get(out, 0) + 1
        classified, arm_rows, learned = stream(name, lk, fresh, labels, glosses, llm, m)
        after, r2 = traps(name, "after the replay", lk, trap_receipts, trap_labels, trap_glosses, llm)
        stats = lookup_stats(arm_rows)
        stats["receipts_no_model_call"] = sum(all(r["decided_by"].startswith("lookup") for r in arm_rows if r["receipt"] == rec.receipt_id)
                                              for rec in fresh if rec.line_items)
        blocked = {}
        for row in arm_rows:
            for why in ("a restricted word", "a restricted reference is the same item", "a restricted reference is as close", "the library's closest category"):
                if row["why"].startswith(why):
                    blocked[why] = blocked.get(why, 0) + 1
        arms[name] = {"replay": {"score": cord_score(classified, fresh, labels), "lookup": stats, "sent_to_model_by": blocked},
                      "traps_committed_memory": before, "traps_after_replay": after,
                      "warm_up": warmed, "learning": learned, "memory_after": len(lk.memory), "risk_after": len(lk.risk)}
        rows += arm_rows
        trap_rows += r1 + r2
        print(f"{name}: replay share {stats['share']}, restricted as safe {stats['restricted_as_safe']}; "
              f"traps decided safe {len(before['restricted_decided_safe'])} / {len(after['restricted_decided_safe'])}")

    own = {"M": own_sets(None, llm), "V3.3": own_sets(lookup(V33, emb), llm)}

    texts = list(dict.fromkeys(normalise(line.description_raw) for r in trap_receipts + fresh for line in r.line_items))
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "t.json").write_text(json.dumps(texts), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "experiments.semantic_v2.embed", "--recompute", str(Path(tmp) / "a.npy"), str(Path(tmp) / "t.json")],
                       check=True, cwd=ROOT, capture_output=True)
        diff = float(np.abs(np.load(Path(tmp) / "a.npy") - np.stack(emb.embed(texts))).max())

    V, M_own, V_own = arms["V3.3"], own["M"], own["V3.3"]
    R = V["replay"]
    bar = {
        "1 own sets: 28/28, 65/65, 0 FP, 0 missed; timing 4/4; dev ≥42; reworded ≥ M":
            V_own["receipts"]["exact"] == 28 and V_own["receipts"]["items"] == 65 and V_own["receipts"]["false_positives"] == 0
            and V_own["receipts"]["missed"] == 0 and all(V_own["timing"].values()) and min(V_own["dev"].values()) >= 42
            and V_own["reworded"]["items"] >= M_own["reworded"]["items"] and V_own["reworded"]["exact"] >= M_own["reworded"]["exact"],
        "2 traps, both memories: no restricted trap decided safe; no more restricted traps missed than M":
            all(not V[k]["restricted_decided_safe"] and len(V[k]["restricted_missed"]) <= len(m_traps["restricted_missed"])
                for k in ("traps_committed_memory", "traps_after_replay")),
        "3 replay: no restricted called safe; ≥99.5% right on sure labels; as accurate as M":
            not R["lookup"]["restricted_as_safe"]
            and (not R["lookup"]["decided_sure"] or R["lookup"]["right_sure"] / R["lookup"]["decided_sure"] >= 0.995)
            and R["score"]["lines_right"] >= m_score["lines_right"] - 2 and R["score"]["false_alarms"] <= m_score["false_alarms"]
            and R["score"]["missed"] <= m_score["missed"],
        "4 useful: ≥35% of replay lines": R["lookup"]["share"] >= 0.35,
        "5 repeatable": diff <= 1e-5,
    }
    metrics = {"config": {"V3.2": V32, "V3.3": V33}, "M": {"replay": m_score, "traps": m_traps}, "arms": arms, "own_sets": own,
               "repeatability": {"texts": len(texts), "largest_difference": diff}, "bar": bar,
               "verdict": "passes" if all(bar.values()) else "fails: " + "; ".join(k for k, ok in bar.items() if not ok),
               "spend_usd": ledger_total(LEDGER)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    for fname, data in (("stream.csv", rows), ("traps.csv", trap_rows)):
        with (RESULTS / fname).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    print("M replay:", m_score, "\nM traps missed:", m_traps["restricted_missed"], "safe flagged:", m_traps["safe_flagged"])
    for name, a in arms.items():
        print(f"\n{name}: replay {a['replay']['score']}\n   lookup {json.dumps({k: v for k, v in a['replay']['lookup'].items() if k != 'wrong'})}"
              f"\n   wrong {a['replay']['lookup']['wrong']}\n   sent to model by {a['replay']['sent_to_model_by']}")
        for k in ("traps_committed_memory", "traps_after_replay"):
            t = a[k]
            print(f"   {k}: decided safe {t['restricted_decided_safe']}; missed {t['restricted_missed']}; to human {t['restricted_to_human']}; "
                  f"word list {t['restricted_sent_to_model_by_word_list']}; safe decided {t['safe_decided']} ({t['safe_decided_right']} right)")
    print("\nown sets:", {k: (v["receipts"]["exact"], v["receipts"]["items"], v["receipts"]["false_positives"], v["receipts"]["missed"],
                              v["dev"], v["reworded"]["items"], v["reworded"]["exact"], v.get("lines_decided_on_32")) for k, v in own.items()})
    print(f"repeatability {diff}; verdict: {metrics['verdict']}; spend ${metrics['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
