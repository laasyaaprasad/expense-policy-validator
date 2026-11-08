"""People's reviews of receipt lines: the queue of lines worth a person's look, and the store of their decisions.

A review changes nothing by itself. It is appended to `reviews.jsonl`, and `make learn`
(validator/learn.py) later applies the reviews to the lookup's memory under the two-key rule. The viewer's Review page
(viewer/review.py) is where a person makes them.

The queue, built from a run's traces and results:
- lines the model was unsure of (below the EXC-03 threshold);
- lines a flag or review cites;
- the lookup's audit sample;
- lines where the lookup and the model's second opinion disagree.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from validator.lookup import LOOKUP_DIR
from validator.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy()
REASONS = ("the AI was unsure (EXC-03)", "cited by a flag or review", "audit sample", "lookup and AI disagree")


def reviews_path(directory: Path = LOOKUP_DIR) -> Path:
    """`VALIDATOR_REVIEWS` overrides the path (the viewer's tests use it, so they never touch the real file)."""
    override = os.environ.get("VALIDATOR_REVIEWS")
    return Path(override) if override else directory / "reviews.jsonl"


def load_reviews(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def review_id(record: dict) -> str:
    key = "\n".join(str(record[k]) for k in ("line_id", "category", "sure", "reviewer", "reviewed_at"))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def add_review(path: Path, item: dict, category: str, sure: bool, reviewer: str) -> dict:
    """Append one person's decision on a queued line. Refuses an unknown category or an anonymous reviewer."""
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("a review needs the reviewer's name")
    if category not in TAXONOMY:
        raise ValueError(f"unknown category {category!r}")
    record = {
        "receipt_id": item["receipt_id"], "line_id": item["line_id"], "text": item["text"],
        "category": category, "restricted": TAXONOMY[category], "sure": bool(sure), "reviewer": reviewer,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "system_category": item.get("category"), "model_category": item.get("model_category"),
        "model_confidence": item.get("model_confidence"), "queued_because": item.get("reasons", []),
    }
    record["review_id"] = review_id(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def review_queue(traces: list[dict], results: list[dict], texts: dict[str, str], every_line: bool = False) -> list[dict]:
    """The lines worth a person's look, one entry per line with every reason it is queued (or every line)."""
    cited = {}
    for r in results:
        if r["disposition"] in ("flag", "review"):
            for e in r["exceptions"]:
                for line_id in e.get("line_ids", []):
                    cited.setdefault(line_id, []).append(e["rule_id"])
    queue = []
    for t in traces:
        uncertain = set(t["engine"].get("uncertain_lines", []))
        for x in t["classify"]["lines"]:
            reasons = []
            if x["line_id"] in uncertain:
                reasons.append(REASONS[0])
            if x["line_id"] in cited:
                reasons.append(f"{REASONS[1]} ({', '.join(sorted(set(cited[x['line_id']])))})")
            if x.get("audit"):
                reasons.append(REASONS[2])
            who = x.get("decided_by", "model")
            by_lookup = who.startswith("lookup")
            # The model's answer for the line: its own decision, or its second opinion on a lookup line; none for code's.
            model = x.get("model_said") if by_lookup else [x["category"], x["confidence"]] if who.startswith("model") else None
            if by_lookup and model and model[0] != x["category"]:
                reasons.append(REASONS[3])
            if reasons or every_line:
                queue.append({"receipt_id": t["receipt_id"], "line_id": x["line_id"], "text": texts.get(x["line_id"], ""),
                              "category": x["category"], "confidence": x["confidence"], "decided_by": x.get("decided_by", "model"),
                              "model_category": model[0] if model else None, "model_confidence": model[1] if model else None,
                              "reasons": reasons})
    return queue
