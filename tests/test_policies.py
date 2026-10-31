"""M2 gate: the policy corpus parses into exactly the rules the data dictionary describes."""

import re
from collections import Counter

import pytest

from evaluation.gold import load_gold
from validator.data import DATA_DIR, POLICY_DIR
from validator.policies import RULE_ID, load_policies, parse_all


@pytest.fixture(scope="module")
def rules():
    return load_policies()


def test_nine_documents():
    assert len(list(POLICY_DIR.glob("*.md"))) == 9


def test_rule_count_matches_data_dictionary(rules):
    stated = re.search(r"defining (\d+) rules", (DATA_DIR / "README.md").read_text())
    assert stated, "data dictionary no longer states a rule count"
    assert len(rules) == int(stated[1]) == 41


def test_every_rule_defined_exactly_once():
    counts = Counter(rule.id for rule in parse_all())
    assert {rule_id: n for rule_id, n in counts.items() if n > 1} == {}


def test_definitions_match_an_independent_scan(rules):
    # A cruder scan than the parser: any line that starts with a bold rule ID.
    scanned = set()
    for path in POLICY_DIR.glob("*.md"):
        scanned |= set(re.findall(r"^\*\*([A-Z]+-\d{2})", path.read_text(), flags=re.MULTILINE))
    assert scanned == set(rules)


def test_no_dangling_references(rules):
    # Every rule ID mentioned anywhere in the corpus, taxonomy or data dictionary is defined.
    sources = [*POLICY_DIR.glob("*.md"), DATA_DIR / "category-taxonomy.md", DATA_DIR / "README.md"]
    mentioned = set()
    for path in sources:
        mentioned |= set(RULE_ID.findall(path.read_text()))
    assert mentioned - set(rules) == set()


def test_gold_rules_are_defined_in_the_cited_document(rules):
    for g in load_gold().values():
        for finding in (*g.exceptions, *g.review_signals):
            assert finding.rule_id in rules, (g.receipt_id, finding.rule_id)
            assert rules[finding.rule_id].doc == finding.policy_doc, (g.receipt_id, finding.rule_id)


def test_rule_texts_are_whole_and_verbatim(rules):
    docs = {path.name: path.read_text() for path in POLICY_DIR.glob("*.md")}
    for rule in rules.values():
        assert rule.title, rule.id
        assert rule.text.startswith(f"**{rule.id} — "), rule.id
        assert rule.text in docs[rule.doc], rule.id
