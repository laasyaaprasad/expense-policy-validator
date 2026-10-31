"""M9 gate on the real receipts, offline: the committed v1 classifications are well formed and are
exactly what the committed cache replays, with no calls."""

from evaluation.items import load_classifications, recipient_problems, structural_problems
from evaluation.oracle import load_attributes
from validator.data import load_receipts
from validator.llm import CACHE_DIR, LLM
from validator.pipeline import OUTPUT_DIR, classification_rows, classify_all, write_jsonl


def test_v1_output_is_well_formed():
    assert structural_problems(load_classifications()) == []


def test_v1_recipients_match_the_hand_annotation():
    assert recipient_problems(load_classifications(), load_attributes()) == []


def test_an_offline_rerun_reproduces_v1_byte_for_byte_with_no_calls(tmp_path):
    ledger, out = tmp_path / "spend.jsonl", tmp_path / "classifications.jsonl"
    glosses, classifications = classify_all(load_receipts(), LLM.offline(cache_dir=CACHE_DIR, ledger_path=ledger))
    write_jsonl(classification_rows(glosses, classifications), out)
    assert out.read_bytes() == (OUTPUT_DIR / "classifications.jsonl").read_bytes()
    assert not ledger.exists()
