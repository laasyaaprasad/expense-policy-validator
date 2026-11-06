"""Experiment 4's logic (experiments/graph_retrieval), offline: no key, no calls."""

import pytest
import yaml

from evaluation import cord
from evaluation.blame import load_classified
from evaluation.gold import load_gold
from evaluation.oracle import load_attributes, oracle_receipts
from experiments.graph_retrieval.__main__ import EXPERIMENT_3_CONFIG, needed, score
from experiments.graph_retrieval.extract import GraphReply, problems, system_prompt
from experiments.graph_retrieval.graph import TripleStore, from_config, from_extraction, rule_node
from experiments.graph_retrieval.retrieve import BM25, graph_retrieve
from validator.data import load_receipts
from validator.policies import load_policies
from validator.retriever import retrieve
from validator.rules_config import RulesConfig, load_rules


@pytest.fixture(scope="module")
def policies():
    return load_policies()


@pytest.fixture(scope="module")
def config():
    return load_rules()


@pytest.fixture(scope="module")
def checkable(config):
    return {r.id for r in config.rules if r.kind != "mechanic"}


def test_the_graph_from_rules_yaml_retrieves_exactly_what_the_submitted_retriever_does(policies, config, checkable):
    store = from_config(config, policies)
    receipts = list(load_classified()) + list(cord.load_system(load_receipts(cord.RECEIPTS_PATH))[0])
    for c in receipts:
        assert graph_retrieve(c, store, config, checkable) == sorted(set(retrieve(c, config, policies).rule_ids) & checkable), c.receipt.receipt_id


def test_the_store_answers_both_queries_and_refuses_unknown_edges():
    store = TripleStore([("rule:A", "cites", "rule:B"), ("rule:C", "cites", "rule:B")])
    assert store.objects("rule:A", "cites") == {"rule:B"} and store.subjects("cites", "rule:B") == {"rule:A", "rule:C"}
    with pytest.raises(ValueError):
        store.add("rule:A", "likes", "rule:B")


def test_region_restriction_and_overrides_from_an_extraction(policies):
    extracted = {"REG-01": {"triggered_by": ["category:alcohol"], "restricted_to": ["APAC"], "overrides": ["ALC-01"],
                            "replaces": [], "takes_lines_from": [], "waived_by": None}}
    store = from_extraction(extracted, policies)
    assert store.objects(rule_node("REG-01"), "restricted_to") == {"region:APAC"}
    assert store.objects(rule_node("REG-01"), "overrides") == {"rule:ALC-01"}


def test_region_filter_keeps_apac_rules_away_from_us_employees(policies, config, checkable):
    store = from_config(config, policies)
    by_id = {c.receipt.receipt_id: c for c in load_classified()}
    assert "REG-01" in graph_retrieve(by_id["RCP-016"], store, config, checkable)  # an IN employee's beer
    assert "REG-01" not in graph_retrieve(by_id["RCP-015"], store, config, checkable)  # a US employee's wine


def test_bm25_ranks_the_obvious_rule_first(policies, checkable):
    bm25 = BM25([policies[i] for i in sorted(checkable)])
    assert bm25.ranked("cigarettes tobacco pack")[0] == "PERS-03"


def test_the_extraction_check_and_prompt(policies, config):
    assert not any(rule_id in system_prompt(config) for rule_id in policies), "a real rule ID leaked into the instructions"
    check = problems(config, set(policies))
    assert check(GraphReply(triggered_by=["category:alcohol"])) == []
    found = " ".join(check(GraphReply(triggered_by=["category:wine"], overrides=["NOPE-1"], waived_by="boss")))
    assert "not allowed" in found and "don't exist" in found and "waived_by" in found


def test_needed_rules_come_from_the_engine_s_own_trace(config, checkable):
    need = needed(oracle_receipts(load_receipts(), load_gold(), load_attributes()), config, checkable)
    assert {"MEAL-01", "ALC-02"} <= need["RCP-015"] and need["RCP-001"] == set()


def test_scoring_counts_recall_and_misses():
    result = score({"R1": ["A", "B"], "R2": ["A"]}, {"R1": {"A"}, "R2": {"A", "C"}})
    assert result["recall"] == {"k": 2, "n": 3} and result["missed"] == {"R2": ["C"]}
    assert result["receipts_full_recall"] == {"k": 1, "n": 2}


def test_experiment_3_s_config_loads_as_a_graph(policies):
    store = from_config(RulesConfig.model_validate(yaml.safe_load(EXPERIMENT_3_CONFIG.read_text())), policies)
    assert store.of("triggered_by")
