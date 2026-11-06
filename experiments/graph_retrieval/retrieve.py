"""The retrievers compared: graph traversal, BM25 and embeddings. Each maps a classified receipt to the
rule IDs the engine should check (PLAN.md, Arms)."""

import math
import re
from collections import Counter

import numpy as np

from experiments.graph_retrieval.graph import ANY_RECEIPT, NAMES_RECIPIENT, TripleStore, rule_node
from validator.models import ClassifiedReceipt
from validator.normaliser import normalise
from validator.policies import Rule
from validator.rules_config import RulesConfig

K = 10  # PLAN.md: fixed in advance, about the structured index's average


def facts(c: ClassifiedReceipt, shared: RulesConfig) -> set[str]:
    """The receipt's fact nodes: where traversal starts."""
    items = c.items.values()
    nodes = {f"category:{i.category}" for i in items} | {f"item_kind:{i.item_kind}" for i in items if i.item_kind}
    nodes |= {f"recipient_type:{i.recipient_type}" for i in items if i.recipient_type}
    if any(i.recipient_name for i in items):
        nodes.add(NAMES_RECIPIENT)
    nodes |= {f"expense_type:{c.receipt.submitted_expense_type}", f"mcc_risk:{shared.mcc_tier(c.receipt.mcc)}", ANY_RECEIPT}
    return nodes


def graph_retrieve(c: ClassifiedReceipt, store: TripleStore, shared: RulesConfig, checkable: set[str], hops: int = 1) -> list[str]:
    """PLAN.md's traversal: rules triggered by the receipt's facts, filtered by the employee's region,
    then `cites` followed `hops` steps out from the rules matched on a specific fact."""
    region = f"region:{shared.region_of(c.receipt.employee_country)}"

    def allowed(node: str) -> bool:
        limits = store.objects(node, "restricted_to")
        return not limits or region in limits

    matched = {node for fact in facts(c, shared) for node in store.subjects("triggered_by", fact) if allowed(node)}
    # As the submitted retriever: a rule that applies to any receipt cites others only as examples,
    # so citations are followed only from rules without that edge.
    frontier = {node for node in matched if ANY_RECEIPT not in store.objects(node, "triggered_by")}
    for _ in range(hops):
        frontier = {cited for node in frontier for cited in store.objects(node, "cites") if allowed(cited)} - matched
        matched |= frontier
    ids = {node.removeprefix("rule:") for node in matched}
    return sorted(ids & checkable)


def query_text(c: ClassifiedReceipt, shared: RulesConfig, glosses: dict[str, str | None]) -> str:
    """What the text retrievers search with: the lines, their glosses and categories, and the receipt's context."""
    parts = []
    for line in c.receipt.line_items:
        item = c.items[line.line_id]
        parts += [normalise(line.description_raw), glosses.get(line.line_id) or "", item.category.replace("_", " "),
                  (item.item_kind or "").replace("_", " "), (item.recipient_type or "").replace("_", " ")]
    parts += [c.receipt.submitted_expense_type, c.receipt.mcc_description, shared.region_of(c.receipt.employee_country)]
    return " ".join(p for p in parts if p)


_TOKEN = re.compile(r"\w+")


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text.replace("_", " "))]


class BM25:
    """Okapi BM25 over the checkable rules' text (k1 1.5, b 0.75)."""

    def __init__(self, rules: list[Rule], k1: float = 1.5, b: float = 0.75):
        self.ids = [r.id for r in rules]
        self.docs = [Counter(tokens(f"{r.title} {r.text}")) for r in rules]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.mean_length = sum(self.lengths) / len(self.lengths)
        frequency = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in frequency.items()}
        self.k1, self.b = k1, b

    def ranked(self, query: str) -> list[str]:
        terms = tokens(query)
        scores = []
        for doc, length in zip(self.docs, self.lengths):
            norm = self.k1 * (1 - self.b + self.b * length / self.mean_length)
            scores.append(sum(self.idf.get(t, 0) * doc[t] * (self.k1 + 1) / (doc[t] + norm) for t in terms if t in doc))
        order = sorted(range(len(self.ids)), key=lambda i: (-scores[i], self.ids[i]))
        return [self.ids[i] for i in order]


def embedding_ranked(query_vector: np.ndarray, rule_vectors: np.ndarray, rule_ids: list[str]) -> list[str]:
    sims = rule_vectors @ query_vector
    order = sorted(range(len(rule_ids)), key=lambda i: (-sims[i], rule_ids[i]))
    return [rule_ids[i] for i in order]
