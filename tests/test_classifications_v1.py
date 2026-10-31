"""M9 gate on the real receipts, offline: the committed v1 classifications are well formed.
That they are exactly what the cache replays is checked with the rest of the run in test_pipeline.py."""

from evaluation.items import load_classifications, recipient_problems, structural_problems
from evaluation.oracle import load_attributes


def test_v1_output_is_well_formed():
    assert structural_problems(load_classifications()) == []


def test_v1_recipients_match_the_hand_annotation():
    assert recipient_problems(load_classifications(), load_attributes()) == []
