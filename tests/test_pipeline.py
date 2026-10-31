"""The committed run is valid, traceable, and exactly what an offline rerun produces."""

import json

import pytest

from validator.engine.engine import CONFIDENCE_THRESHOLD
from validator.llm import CACHE_DIR, LLM
from validator.pipeline import OUTPUT_DIR, run, write_run
from validator.reporter import ReceiptResult
from validator.rules_config import policy_version

OUTPUTS = ("classifications.jsonl", "retrieval.jsonl", "results.jsonl")


@pytest.fixture(scope="module")
def committed():
    with (OUTPUT_DIR / "results.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_every_record_validates_against_the_schema(committed):
    assert len(committed) == 32
    for record in committed:
        ReceiptResult.model_validate(record)


def test_every_record_says_what_produced_it(committed):
    for record in committed:
        assert record["provider"] == "deepseek" and record["model"] == "deepseek-flash"
        assert record["prompt_version"] == "classify-v1"
        assert record["policy_version"] == policy_version()
        assert record["confidence_threshold"] == CONFIDENCE_THRESHOLD


def test_an_offline_rerun_is_byte_identical_with_no_calls(tmp_path):
    ledger = tmp_path / "spend.jsonl"
    write_run(run(LLM.offline(cache_dir=CACHE_DIR, ledger_path=ledger)), tmp_path)
    for name in OUTPUTS:
        assert (tmp_path / name).read_bytes() == (OUTPUT_DIR / name).read_bytes(), name
    assert not ledger.exists()
