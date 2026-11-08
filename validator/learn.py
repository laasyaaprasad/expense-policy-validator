"""Apply people's reviews to the lookup's memory: `make learn` (python -m validator.learn [--client NAME] [--dry-run]).

Each review not yet applied (its ID isn't in the client's learn_log.jsonl) goes through the two-key rule
(Lookup.learn in validator/lookup.py):
- an unsure review is left out;
- a line the person marks restricted becomes a restricted reference, shared by every client, which only ever blocks;
- a safe line is remembered only when the model agreed with the person at confidence 0.9 or more;
- anything else is left out, so the model's mistakes are never copied in.

A review that contradicts a remembered line removes that line first (a correction). A line a person once confirmed
restricted is never turned safe by a later review: restricted references are removed only by hand.

It writes the client's memory.csv and learn_log.jsonl, and the new vectors into vectors.npz. `--dry-run` reports the
same outcomes and writes nothing. If a review's text or words have no vector and the embedding model isn't installed
(uv sync --group semantic), it refuses before changing anything. Rerun the pipeline (`make run`) to use what it learned.
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from validator.lookup import DEFAULT_CLIENT, LOOKUP_DIR, MEMORY_FIELDS, TAXONOMY, Lookup, client_dir, item_words, read_memory
from validator.normaliser import normalise
from validator.reviews import load_reviews, reviews_path


def learn(client: str = DEFAULT_CLIENT, directory: Path = LOOKUP_DIR, reviews: Path | None = None, dry_run: bool = False) -> tuple[int, list[dict]]:
    """Returns (exit code, one record per review applied)."""
    lookup = Lookup(directory, client=client)
    lookup._load_vectors()
    if lookup.ready is not True:
        print("refused: the library's vectors aren't available, so the lookup can't load.")
        return 2, []
    folder = client_dir(client, directory)
    log_path = folder / "learn_log.jsonl"
    applied = {r["review_id"] for r in load_reviews(log_path)}
    pending = [r for r in load_reviews(reviews or reviews_path(client, directory)) if r["review_id"] not in applied]
    if not pending:
        print(f"nothing to learn for client {client!r}: every review is already applied.")
        return 0, []

    if dry_run:
        lookup.embedder.save = lambda: None  # a dry run writes nothing, vectors included
    texts = list(dict.fromkeys(t for r in pending if r["sure"] for t in [normalise(r["text"]), *item_words(r["text"])]))
    available = lookup.embedder.available()
    missing = [t for t in texts if lookup.embedder.key(t) not in lookup.embedder._cache]
    if missing and not available:
        print(f"refused: {len(missing)} texts or words have no vector and the embedding model isn't installed "
              f"(uv sync --group semantic), e.g. {missing[:3]}. Nothing was changed.")
        return 2, []
    if missing and not dry_run:
        lookup.embedder.embed(missing)

    records, removed_rows, added_rows = [], [], []
    for r in pending:
        text, category = normalise(r["text"]), r["category"]
        removed = []
        if not r["sure"]:
            outcome = "left out: unsure review"
        elif not TAXONOMY[category] and any(t == text for t, _ in lookup.risk):
            outcome = "left out: a person once confirmed this line restricted; restricted references are removed only by hand"
        else:
            removed = lookup.forget(text, keep=category)
            outcome = lookup.learn(text, category, True, r.get("model_category"), r.get("model_confidence"))
        removed_rows += removed
        if outcome in ("memory", "risk"):
            added_rows.append({"role": "remember" if outcome == "memory" else "risk", "category": category, "text": text,
                               "reviewed_line": r["line_id"]})
        records.append({"review_id": r["review_id"], "line_id": r["line_id"], "text": text, "category": category,
                        "reviewer": r["reviewer"], "outcome": outcome, "removed": [list(x) for x in removed],
                        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    for rec in records:
        note = f" (removed {rec['removed']})" if rec["removed"] else ""
        print(f"{rec['line_id']} \"{rec['text']}\" → {rec['category']}: {rec['outcome']}{note}")
    counts = {}
    for rec in records:
        key = rec["outcome"].split(":")[0]
        counts[key] = counts.get(key, 0) + 1
    print(f"{len(records)} reviews: {counts}" + ("; dry run, nothing written" if dry_run else ""))
    if dry_run:
        return 0, records

    drop = {(t, c) for t, c in removed_rows}
    rows = [row for row in read_memory(lookup.memory_path) if not (row["role"] == "remember" and (row["text"], row["category"]) in drop)]
    lookup.memory_path.parent.mkdir(parents=True, exist_ok=True)
    with lookup.memory_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MEMORY_FIELDS)
        w.writeheader()
        w.writerows({k: row[k] for k in MEMORY_FIELDS} for row in rows + added_rows)
    with log_path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {lookup.memory_path} and {log_path}. Rerun the pipeline (make run) to use it.")
    return 0, records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--client", default=DEFAULT_CLIENT)
    ap.add_argument("--directory", type=Path, default=LOOKUP_DIR)
    ap.add_argument("--reviews", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    code, _ = learn(args.client, args.directory, args.reviews, args.dry_run)
    return code


if __name__ == "__main__":
    sys.exit(main())
