"""How each decision was reached: one record per receipt, written beside results.jsonl.

A record follows its receipt through the pipeline. It names the translation and classification calls
by cache key, so the exact request and reply can be read from cache/. It also records any retry and
why, any confidence fallback, each line's runner-up categories, the rules retrieved, and what every
rule's check found, with the numbers behind it and the receipts that share a cross-receipt total.

It holds IDs, categories, amounts and rule IDs, never line text or names, so the same record could go
to a production log as it is (docs/aws-design.md). It is deterministic, so an offline rerun writes the
same bytes. What changes from run to run (when, which commit, how long, cache hits) goes to the run log.
"""

import hashlib
import json
import subprocess
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime

from validator.classifier import Classification
from validator.data import ROOT
from validator.models import Decision
from validator.retriever import Retrieval

RUN_LOG = ROOT / "logs" / "runs.jsonl"
NAME_KEYS = {"recipient", "recipient_name", "recipients"}


def receipt_trace(c: Classification, glosses: dict[str, str], translation: dict | None, retrieval: Retrieval,
                  engine: dict, decision: Decision) -> dict:
    receipt = c.receipt.receipt
    return {
        "receipt_id": receipt.receipt_id,
        "disposition": decision.disposition,
        "translate": None if translation is None else {
            "lines": [line.line_id for line in receipt.line_items if line.line_id in glosses],
            "call": translation["call"],
            "retried_after": translation["retried_after"],
        },
        "classify": {
            "call": c.cache_key,
            "prompt_version": c.prompt_version,
            "retried_after": list(c.retried_after),
            "confidence_fallback": c.confidence_fallback,
            "lines": [{"line_id": line.line_id, "category": c.receipt.items[line.line_id].category,
                       "confidence": c.receipt.items[line.line_id].confidence,
                       "alternatives": c.alternatives.get(line.line_id, [])} for line in receipt.line_items],
        },
        "retrieve": {"rules": list(retrieval.rule_ids)},
        "engine": _without_names(engine),
    }


def trace_line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)


def _without_names(value):
    """Recipient names in a rule's facts become "<name>": the trace says a line was named, not who."""
    if isinstance(value, dict):
        return {key: _redacted(item) if key in NAME_KEYS else _without_names(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_without_names(item) for item in value]
    return value


def _redacted(names):
    if isinstance(names, (list, tuple)):
        return ["<name>" for _ in names]
    return "<name>" if names else names


@contextmanager
def timed(seconds: dict[str, float], stage: str):
    start = time.perf_counter()
    yield
    seconds[stage] = time.perf_counter() - start


def run_record(*, started: datetime, seconds: dict[str, float], provider: str, model: str, dispositions: list[str],
               classifications: list[Classification], translations: dict[str, dict], traces: bytes) -> dict:
    """One line of the run log: what made this run, how long each stage took, and what the calls did."""
    calls = {
        "translate": Counter("cached" if t["cached"] else "live" for t in translations.values()),
        "classify": Counter("cached" if c.cached else "live" for c in classifications if c.cache_key),
    }
    return {
        "run_id": uuid.uuid4().hex[:12],
        "started": started.isoformat(timespec="seconds"),
        "commit": _commit(),
        "provider": provider,
        "model": model,
        "receipts": len(dispositions),
        "dispositions": {d: dispositions.count(d) for d in ("flag", "review", "pass")},
        "stage_seconds": {stage: round(s, 3) for stage, s in seconds.items()},
        "calls": {stage: {"cached": n["cached"], "live": n["live"]} for stage, n in calls.items()},
        "retries": sum(bool(c.retried_after) for c in classifications) + sum(bool(t["retried_after"]) for t in translations.values()),
        "confidence_fallbacks": sum(c.confidence_fallback is not None for c in classifications),
        "traces_sha256": hashlib.sha256(traces).hexdigest()[:16],
    }


def _commit() -> str | None:
    """The code's git commit, with "+changes" when tracked files differ from it."""
    try:
        git = lambda *args: subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        head, changes = git("rev-parse", "--short", "HEAD"), git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return None
    return head + ("+changes" if changes else "")
