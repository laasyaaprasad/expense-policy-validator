"""Recalibrate the lookup's word threshold (τ_word) on a client's own reviewed receipts.

    uv run --group semantic python -m validator.calibrate --reviewed reviewed.jsonl [--directory rules/lookup] [--write] [--client-reviewed]

`reviewed.jsonl` has one line per reviewed receipt line: {"receipt_id", "text", "category", "sure"}; `sure` is
optional and defaults to true. Only lines a person decided belong in it, never the model's own answers.

The rule is experiment 12's (experiments/semantic_v4/PLAN.md), unchanged:
1. A floor: above the highest word similarity between a restricted and an unrestricted single-word term of the frozen
   keyword list (rules/keywords.yaml), plus 0.01, so no drink word can stand in for another.
2. On the reviewed lines, pairs on different receipts with sentence similarity of at least 0.5: the lowest τ_word at
   which the pairs passing the word test agree on category at least 99.9% of the time, with no disagreement on
   whether the line is restricted. τ_word is the higher of the two.

It refuses (and writes nothing) with fewer than 1,000 sure reviewed lines or 300 matched pairs: too little to trust,
so τ_word stays as it is. With `--write` it replaces `tau_word` in config.yaml and records the calibration in
calibration.json; it never changes `mode` and never touches the restricted block (block_tau), which
is a margin of caution: lowering it is always safe, raising it needs its own evidence.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import yaml

from validator.baseline import load_terms
from validator.data import read_jsonl
from validator.lookup import LOOKUP_DIR, TAXONOMY, Embedder, item_words
from validator.normaliser import normalise

AGREE, MIN_LINES, MIN_PAIRS = 0.999, 1000, 300


def floor(embed) -> tuple[float, list]:
    terms = load_terms()
    single = lambda restricted: sorted({item_words(t.text)[0] for t in terms if t.restricted == restricted and len(item_words(t.text)) == 1})
    r, u = single(True), single(False)
    sims = np.stack(embed(r)) @ np.stack(embed(u)).T
    top = sorted(((float(sims[a, b]), r[a], u[b]) for a in range(len(r)) for b in range(len(u))), reverse=True)[:5]
    return top[0][0] + 0.01, [(round(s, 4), a, b) for s, a, b in top]


def calibrate(lines: list[dict], embed, tau_floor: float) -> tuple[float | None, dict]:
    """τ_word from reviewed lines ({"receipt_id", "text", "category"}), or None if the data can't support one."""
    vectors = np.stack(embed([normalise(x["text"]) for x in lines]))
    words = [item_words(x["text"]) for x in lines]
    vocab = sorted({w for ws in words for w in ws})
    index = {w: i for i, w in enumerate(vocab)}
    wv = np.stack(embed(vocab)) if vocab else np.zeros((0, vectors.shape[1]), np.float32)
    W = wv @ wv.T
    ids = [np.array([index[w] for w in ws], dtype=int) for ws in words]
    sims = vectors @ vectors.T
    pairs = []
    for i in range(len(lines)):
        if not len(ids[i]):
            continue
        for j in np.nonzero(sims[i, i + 1:] >= 0.5)[0] + i + 1:
            if lines[i]["receipt_id"] == lines[j]["receipt_id"] or not len(ids[j]):
                continue
            s = W[np.ix_(ids[i], ids[j])]
            score = float(min(s.max(axis=1).min(), s.max(axis=0).min()))
            if score >= tau_floor:
                a, b = lines[i]["category"], lines[int(j)]["category"]
                pairs.append((score, a == b, TAXONOMY[a] == TAXONOMY[b], i, int(j)))
    pairs.sort(reverse=True)
    flag_floor = max((s for s, _, flag, _, _ in pairs if not flag), default=-1.0)
    best, agree = None, 0
    for n, (s, same, *_rest) in enumerate(pairs, start=1):
        agree += same
        if s > flag_floor and agree / n >= AGREE:
            best = (s, n, agree)
    info = {"reviewed_lines": len(lines), "pairs_at_or_above_floor": len(pairs), "floor": round(tau_floor, 6),
            "highest_restricted_disagreement": round(flag_floor, 4),
            "disagreeing_pairs": [(round(s, 4), lines[i]["text"], lines[i]["category"], lines[j]["text"], lines[j]["category"])
                                  for s, same, _, i, j in pairs if not same][:10]}
    if best is None:
        return None, {**info, "refused": "no threshold above the floor gives 99.9% agreement with no restricted disagreement"}
    info.update({"pairs_at_or_above": best[1], "agreeing": best[2]})
    return max(tau_floor, best[0]), info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reviewed", type=Path, required=True)
    ap.add_argument("--directory", type=Path, default=LOOKUP_DIR, help="the client's lookup directory (config.yaml, memory.csv)")
    ap.add_argument("--cache", type=Path, default=None, help="where to cache the vectors (default: <directory>/calibration-cache.npz)")
    ap.add_argument("--write", action="store_true", help="write τ_word and the calibration record into config.yaml")
    ap.add_argument("--client-reviewed", action="store_true", help="the lines are the client's own reviewers' decisions")
    args = ap.parse_args(argv)

    path = args.directory / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    lines = [{"receipt_id": r["receipt_id"], "text": r["text"], "category": r["category"]}
             for r in read_jsonl(args.reviewed) if r.get("sure", True) and r["category"] in TAXONOMY]
    if len(lines) < MIN_LINES:
        print(f"refused: {len(lines)} sure reviewed lines, fewer than {MIN_LINES}. τ_word is unchanged; collect more reviews.")
        return 2
    emb = Embedder(config["model"], args.cache or args.directory / "calibration-cache.npz")
    texts = list(dict.fromkeys([normalise(x["text"]) for x in lines] + [w for x in lines for w in item_words(x["text"])]))
    if any(v is None for v in emb.embed(texts)):
        print("refused: the embedding model isn't installed (uv sync --group semantic).")
        return 2
    tau_floor, closest = floor(emb.embed)
    tau, info = calibrate(lines, emb.embed, tau_floor)
    print(json.dumps({"tau_word": tau, "floor_pairs": closest, **{k: v for k, v in info.items() if k != "disagreeing_pairs"}}, ensure_ascii=False))
    if tau is None:
        print(f"refused: {info['refused']}. τ_word is unchanged.")
        return 2
    if info["pairs_at_or_above"] < MIN_PAIRS:
        print(f"refused: only {info['pairs_at_or_above']} matched pairs, fewer than {MIN_PAIRS}. τ_word is unchanged.")
        return 2
    print(f"τ_word {tau:.6f} (was {config.get('tau_word')})")
    if args.write:
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"^tau_word: .*$", f"tau_word: {round(tau, 6)}", text, flags=re.M), encoding="utf-8")
        record = {"date": date.today().isoformat(), "reviewed": str(args.reviewed), "client_reviewed": args.client_reviewed, "tau_word": round(tau, 6),
                  **{k: info[k] for k in ("reviewed_lines", "pairs_at_or_above", "agreeing", "floor")}}
        (args.directory / "calibration.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        print(f"wrote τ_word to {path} and the record to {args.directory / 'calibration.json'}; mode is still {config.get('mode', 'decide')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
