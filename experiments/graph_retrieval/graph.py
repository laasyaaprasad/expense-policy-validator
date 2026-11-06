"""The policy graph: an in-memory triple store, and builders from rules.yaml or from an extraction.

A triple is (subject, predicate, object), with nodes named by kind: `rule:ALC-01`,
`category:alcohol`, `expense_type:Meals - Team`, `region:APAC`, `mcc_risk:high`,
`fact:names_recipient`, `fact:any_receipt`, `reference:pre_approval`. A graph database such as Neo4j
would hold the same triples and answer the same two queries (PLAN.md, The graph).
"""

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from validator.policies import Rule
from validator.rules_config import AppliesTo, RulesConfig

PREDICATES = ("triggered_by", "restricted_to", "cites", "overrides", "replaces", "takes_lines_from", "waived_by")
NAMES_RECIPIENT, ANY_RECEIPT = "fact:names_recipient", "fact:any_receipt"


class TripleStore:
    def __init__(self, triples: Iterable[tuple[str, str, str]] = ()):
        self.triples: set[tuple[str, str, str]] = set()
        self._out: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._in: dict[tuple[str, str], set[str]] = defaultdict(set)
        for triple in triples:
            self.add(*triple)

    def add(self, subject: str, predicate: str, obj: str) -> None:
        if predicate not in PREDICATES:
            raise ValueError(f"unknown predicate {predicate!r}")
        self.triples.add((subject, predicate, obj))
        self._out[(subject, predicate)].add(obj)
        self._in[(obj, predicate)].add(subject)

    def objects(self, subject: str, predicate: str) -> set[str]:
        """MATCH (subject)-[predicate]->(o) RETURN o"""
        return set(self._out.get((subject, predicate), ()))

    def subjects(self, predicate: str, obj: str) -> set[str]:
        """MATCH (s)-[predicate]->(obj) RETURN s"""
        return set(self._in.get((obj, predicate), ()))

    def of(self, predicate: str) -> set[tuple[str, str, str]]:
        return {t for t in self.triples if t[1] == predicate}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("subject\tpredicate\tobject\n" + "".join(f"{s}\t{p}\t{o}\n" for s, p, o in sorted(self.triples)),
                        encoding="utf-8")


def rule_node(rule_id: str) -> str:
    return f"rule:{rule_id}"


def trigger_nodes(applies: AppliesTo) -> list[str]:
    """The fact nodes a rule's `applies_to` names, other than regions."""
    nodes = [f"category:{c}" for c in applies.categories] + [f"item_kind:{k}" for k in applies.item_kinds]
    nodes += [f"recipient_type:{t}" for t in applies.recipient_types] + [f"expense_type:{e}" for e in applies.expense_types]
    nodes += [f"mcc_risk:{m}" for m in applies.mcc_risk]
    if applies.names_recipient:
        nodes.append(NAMES_RECIPIENT)
    if applies.always:
        nodes.append(ANY_RECEIPT)
    return nodes


def cites_triples(policies: dict[str, Rule]) -> list[tuple[str, str, str]]:
    """Rule-to-rule citations from the parser: a deterministic reading of the policy text."""
    return [(rule_node(r.id), "cites", rule_node(m)) for r in policies.values() for m in sorted(r.mentions) if m in policies]


def from_config(config: RulesConfig, policies: dict[str, Rule]) -> TripleStore:
    """The graph a config implies: the control arm (from rules.yaml) and the experiment 3 arm."""
    store = TripleStore(cites_triples(policies))
    for rule in config.rules:
        if rule.kind == "mechanic":
            continue
        node = rule_node(rule.id)
        for fact in trigger_nodes(rule.applies_to):
            store.add(node, "triggered_by", fact)
        for region in rule.applies_to.regions:
            store.add(node, "restricted_to", f"region:{region}")
        for predicate, targets in (("overrides", rule.overrides), ("replaces", rule.replaces), ("takes_lines_from", rule.line_overrides)):
            for target in targets:
                store.add(node, predicate, rule_node(target))
        if rule.requires_reference:
            store.add(node, "waived_by", f"reference:{rule.requires_reference}")
    return store


def from_extraction(extracted: dict[str, dict], policies: dict[str, Rule]) -> TripleStore:
    """The graph an LLM read off the policy text (extract.py), plus the parser's citations."""
    store = TripleStore(cites_triples(policies))
    for rule_id, e in extracted.items():
        node = rule_node(rule_id)
        for fact in e["triggered_by"]:
            store.add(node, "triggered_by", fact)
        for region in e["restricted_to"]:
            store.add(node, "restricted_to", f"region:{region}")
        for predicate in ("overrides", "replaces", "takes_lines_from"):
            for target in e[predicate]:
                store.add(node, predicate, rule_node(target))
        if e["waived_by"]:
            store.add(node, "waived_by", f"reference:{e['waived_by']}")
    return store
