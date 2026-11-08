"""Run experiment 12: `make semantic-v4` (uv run --group semantic python -m experiments.semantic_v4).

Calibrates τ_word (a floor from the frozen keyword list, then experiment 10's 99.9% rule on the 800 CORD receipts used
so far), then runs version 4 ("every word accounted for, by meaning") against the model on every line (M), the
pipeline's lookup (V3) and exact word sets (A) on the blind trap set, experiment 11's trap set, a replay of
experiment 10's stream and the project's own sets. The plan and the bar are in PLAN.md.
"""

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from evaluation import cord
from experiments.semantic_v2.__main__ import cord_score
from experiments.semantic_v31.__main__ import lookup, lookup_stats, stream
from experiments.semantic_v33.__main__ import own_sets, traps
from validator.baseline import load_terms
from validator.classifier import classify
from validator.data import ROOT, load_receipts
from validator.llm import CACHE_DIR, LLM, LLMError, ledger_total
from validator.lookup import TAXONOMY, Embedder, Lookup, item_words
from validator.normaliser import normalise
from validator.translate import translate_all

HERE = Path(__file__).resolve().parent
RESULTS, LEDGER = HERE / "results", HERE / "spend.jsonl"
BASE_CACHES = [ROOT / "experiments" / d / "cache" / "vectors.npz" for d in ("semantic_v32", "semantic_v33")]
AGREE, BLOCK_TAU = 0.999, 0.854605


class LayeredEmbedder(Embedder):
    """Reads experiments 10 and 11's committed vectors too, and saves only the new ones here."""

    def __init__(self, model: dict):
        super().__init__(model, HERE / "cache" / "vectors.npz")
        self.base = {}
        for path in BASE_CACHES:
            self.base.update(Embedder(model, path)._cache)
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


def word_matrix(words: list[str], emb: Embedder) -> tuple[dict[str, int], np.ndarray]:
    vecs = emb.embed(words)
    return {w: i for i, w in enumerate(words)}, np.stack(vecs)


def floor(emb: Embedder) -> tuple[float, dict]:
    """Above the highest word similarity between a restricted and an unrestricted single-word term of keywords.yaml."""
    terms = load_terms()
    single = lambda restricted: sorted({item_words(t.text)[0] for t in terms if t.restricted == restricted and len(item_words(t.text)) == 1})
    r, u = single(True), single(False)
    sims = np.stack(emb.embed(r)) @ np.stack(emb.embed(u)).T
    i, j = np.unravel_index(int(np.argmax(sims)), sims.shape)
    top = sorted(((float(sims[a, b]), r[a], u[b]) for a in range(len(r)) for b in range(len(u))), reverse=True)[:8]
    return float(sims[i, j]) + 0.01, {"restricted_terms": len(r), "unrestricted_terms": len(u),
                                     "closest_pairs": [(round(s, 4), a, b) for s, a, b in top]}


def calibrate(lines: list[dict], emb: Embedder, tau_floor: float) -> tuple[float, dict]:
    """Experiment 10's rule on word scores: the lowest τ_word (≥ the floor) at which pairs passing the word test agree
    on category ≥ 99.9%, with no restricted-flag disagreement at or above it."""
    vectors = np.stack(emb.embed([normalise(x["text"]) for x in lines]))
    words = [item_words(x["text"]) for x in lines]
    index, wv = word_matrix(sorted({w for ws in words for w in ws}), emb)
    W = wv @ wv.T
    ids = [np.array([index[w] for w in ws]) for ws in words]
    sims = vectors @ vectors.T
    pairs = []
    for i in range(len(lines)):
        if not len(ids[i]):
            continue
        for j in np.nonzero(sims[i, i + 1:] >= 0.5)[0] + i + 1:
            if lines[i]["receipt"] == lines[j]["receipt"] or not len(ids[j]):
                continue
            s = W[np.ix_(ids[i], ids[j])]
            score = float(min(s.max(axis=1).min(), s.max(axis=0).min()))
            if score >= tau_floor:
                a, b = lines[i]["category"], lines[j]["category"]
                pairs.append((score, a == b, TAXONOMY[a] == TAXONOMY[b], i, int(j)))
    pairs.sort(reverse=True)
    flag_floor = max((s for s, _, flag, _, _ in pairs if not flag), default=-1.0)
    best, agree = None, 0
    for n, (s, same, *_rest) in enumerate(pairs, start=1):
        agree += same
        if s > flag_floor and agree / n >= AGREE:
            best = (s, n, agree)
    tau = max(tau_floor, best[0]) if best else 1.01
    disagreeing = [(round(s, 4), lines[i]["text"], lines[i]["category"], lines[j]["text"], lines[j]["category"])
                   for s, same, _, i, j in pairs if not same and s >= tau]
    flagged = [(round(s, 4), lines[i]["text"], lines[i]["category"], lines[j]["text"], lines[j]["category"])
               for s, _, flag, i, j in pairs if not flag][:5]
    return tau, {"pairs_at_or_above_floor": len(pairs), "pairs_at_or_above": best[1] if best else 0, "agreeing": best[2] if best else 0,
                 "highest_flag_disagreement": round(flag_floor, 4), "flag_disagreements": flagged, "disagreeing_pairs_at_or_above": disagreeing}


def main() -> int:
    split = lambda d: (load_receipts(ROOT / d / "receipts.jsonl"), cord.load_labels(ROOT / d / "labels.jsonl"))
    blind, blind_labels = split("experiments/semantic_v4/traps")
    old, old_labels = split("experiments/semantic_v33/traps")
    seen = [split("cord"), split("cord/test"), split("cord/train"), split("cord/train2")]
    warm, warm_labels = split("cord/train2")
    fresh, labels = split("cord/train3")

    emb = LayeredEmbedder(Lookup().config["model"])
    # One batch for every word this run needs, so the cache is written once.
    all_texts = [line.description_raw for rs, _ in seen + [(fresh, None), (blind, None), (old, None)] for r in rs for line in r.line_items]
    all_texts += [t for t, _ in Lookup().memory + Lookup().risk + Lookup().library]
    emb.embed(list(dict.fromkeys(w for t in all_texts for w in item_words(t))))

    tau_floor, floor_info = floor(emb)
    calib = [{"receipt": r.receipt_id, "text": line.description_raw, "category": lab[line.line_id]["category"]}
             for rs, lab in seen for r in rs for line in r.line_items if lab[line.line_id]["sure"]]
    tau_word, calib_info = calibrate(calib, emb, tau_floor)
    print(f"floor {tau_floor:.4f} {floor_info['closest_pairs'][:3]}; tau_word {tau_word:.4f}: {calib_info['pairs_at_or_above']} of "
          f"{calib_info['pairs_at_or_above_floor']} pairs, {calib_info['agreeing']} agreeing; flag floor {calib_info['highest_flag_disagreement']}; "
          f"disagreeing above: {calib_info['disagreeing_pairs_at_or_above']}")

    v4 = {"memory_rule": "word_by_word", "tau_word": round(tau_word, 6), "block_tau": BLOCK_TAU}
    arms_config = {"V3": {}, "A": {"memory_rule": "exact_words"}, "V4": v4, "V4 + risk words": {**v4, "risk_words": True}}

    llm = client()
    g = {name: translate_all(rs, llm) for name, rs in (("blind", blind), ("old", old), ("warm", warm), ("fresh", fresh))}
    m_warm = {r.receipt_id: classify(r, g["warm"], llm) for r in warm}
    m = {r.receipt_id: classify(r, g["fresh"], llm) for r in fresh}
    m_score = cord_score([m[r.receipt_id].receipt for r in fresh], fresh, labels)
    trap_sets = {"blind": (blind, blind_labels, g["blind"]), "experiment 11": (old, old_labels, g["old"])}
    m_traps, trap_rows = {}, []
    for t, (rs, lab, gl) in trap_sets.items():
        m_traps[t], rows_t = traps("M", "none", None, rs, lab, gl, llm)
        trap_rows += [{**x, "set": t} for x in rows_t]

    arms, rows = {}, []
    for name, overrides in arms_config.items():
        lk = lookup(overrides, emb)
        result = {}
        for t, (rs, lab, gl) in trap_sets.items():
            result[f"{t} traps, committed memory"], rows_t = traps(name, "committed", lk, rs, lab, gl, llm)
            trap_rows += [{**x, "set": t} for x in rows_t]
        warmed = {}
        for r in warm:  # review R400–R599 first: not scored
            for line in r.line_items:
                mi, lab = m_warm[r.receipt_id].receipt.items[line.line_id], warm_labels[line.line_id]
                out = lk.learn(line.description_raw, lab["category"], lab["sure"], mi.category, mi.confidence)
                warmed[out] = warmed.get(out, 0) + 1
        classified, arm_rows, learned = stream(name, lk, fresh, labels, g["fresh"], llm, m)
        for t, (rs, lab, gl) in trap_sets.items():
            result[f"{t} traps, memory after the replay"], rows_t = traps(name, "after the replay", lk, rs, lab, gl, llm)
            trap_rows += [{**x, "set": t} for x in rows_t]
        stats = lookup_stats(arm_rows)
        stats.pop("memory_matches_failing_the_word_test")
        stats["receipts_no_model_call"] = sum(all(r["decided_by"].startswith("lookup") for r in arm_rows if r["receipt"] == rec.receipt_id)
                                              for rec in fresh if rec.line_items)
        why = {}
        for row in arm_rows:
            if not row["decided_by"].startswith("lookup"):
                key = row["why"].split(" (")[0] if row["why"] else row["decided_by"]
                why[key] = why.get(key, 0) + 1
        arms[name] = {"replay": {"score": cord_score(classified, fresh, labels), "lookup": stats, "sent_to_model_because": why},
                      **result, "warm_up": warmed, "learning": learned, "memory_after": len(lk.memory), "risk_after": len(lk.risk)}
        rows += arm_rows
        print(f"{name}: replay share {stats['share']}, restricted as safe {stats['restricted_as_safe']}; traps decided safe "
              + "; ".join(f"{k}: {len(v['restricted_decided_safe'])}" for k, v in result.items()))

    own = {"M": own_sets(None, llm), "V4": own_sets(lookup(v4, emb), llm)}

    texts = list(dict.fromkeys([normalise(line.description_raw) for r in blind + fresh for line in r.line_items]
                               + sorted({w for r in blind + fresh for line in r.line_items for w in item_words(line.description_raw)})))
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "t.json").write_text(json.dumps(texts), encoding="utf-8")
        subprocess.run([sys.executable, "-m", "experiments.semantic_v2.embed", "--recompute", str(Path(tmp) / "a.npy"), str(Path(tmp) / "t.json")],
                       check=True, cwd=ROOT, capture_output=True)
        diff = float(np.abs(np.load(Path(tmp) / "a.npy") - np.stack(emb.embed(texts))).max())

    V, M_own, V_own = arms["V4"], own["M"], own["V4"]
    R = V["replay"]
    trap_keys = [k for k in V if "traps" in k]
    bar = {
        "1 own sets: 28/28, 65/65, 0 FP, 0 missed; timing 4/4; dev ≥42; reworded ≥ M":
            V_own["receipts"]["exact"] == 28 and V_own["receipts"]["items"] == 65 and V_own["receipts"]["false_positives"] == 0
            and V_own["receipts"]["missed"] == 0 and all(V_own["timing"].values()) and min(V_own["dev"].values()) >= 42
            and V_own["reworded"]["items"] >= M_own["reworded"]["items"] and V_own["reworded"]["exact"] >= M_own["reworded"]["exact"],
        "2 both trap sets, both memories: no restricted trap decided safe; no more missed than M":
            all(not V[k]["restricted_decided_safe"] and len(V[k]["restricted_missed"]) <= len(m_traps[k.split(" traps")[0]]["restricted_missed"])
                for k in trap_keys),
        "3 replay: no restricted called safe; ≥99.5% right on sure labels; as accurate as M":
            not R["lookup"]["restricted_as_safe"]
            and (not R["lookup"]["decided_sure"] or R["lookup"]["right_sure"] / R["lookup"]["decided_sure"] >= 0.995)
            and R["score"]["lines_right"] >= m_score["lines_right"] - 2 and R["score"]["false_alarms"] <= m_score["false_alarms"]
            and R["score"]["missed"] <= m_score["missed"],
        "4 meaning adds something: V4 decides more replay lines than A": R["lookup"]["decided"] > arms["A"]["replay"]["lookup"]["decided"],
        "5 repeatable": diff <= 1e-5,
    }
    metrics = {"tau_word": tau_word, "floor": {"value": tau_floor, **floor_info}, "calibration": calib_info, "config": v4,
               "M": {"replay": m_score, "traps": m_traps}, "arms": arms, "own_sets": own,
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
    print("\nM replay:", m_score)
    for t, v in m_traps.items():
        print(f"M {t} traps: missed {v['restricted_missed']}; to human {v['restricted_to_human']}; safe flagged {v['safe_flagged']}")
    for name, a in arms.items():
        print(f"\n{name}: replay {a['replay']['score']}\n   lookup {json.dumps({k: v for k, v in a['replay']['lookup'].items() if k != 'wrong'})}"
              f"\n   wrong {a['replay']['lookup']['wrong']}\n   sent to model because {a['replay']['sent_to_model_because']}")
        for k, t in a.items():
            if "traps" in k:
                print(f"   {k}: decided safe {t['restricted_decided_safe']}; missed {t['restricted_missed']}; to human {t['restricted_to_human']}; "
                      f"safe decided {t['safe_decided']} ({t['safe_decided_right']} right)")
    print("\nown sets:", {k: (v["receipts"]["exact"], v["receipts"]["items"], v["receipts"]["false_positives"], v["receipts"]["missed"],
                              v["dev"], v["reworded"]["items"], v["reworded"]["exact"], v.get("lines_decided_on_32")) for k, v in own.items()})
    print(f"repeatability {diff}; verdict: {metrics['verdict']}; spend ${metrics['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
