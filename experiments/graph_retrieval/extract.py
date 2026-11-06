"""The graph extraction: one model call per checkable rule, reading the policy text only.

The model lists the rule's edges as json: the receipt facts that trigger it, the regions it is
restricted to, the rules it overrides, replaces or takes lines from, and the reference kind that
waives it. It is given the allowed node names and one made-up example, never rules.yaml, the answer
key or a receipt. Citations come from the parser instead (graph.py).
"""

import json
from typing import get_args

from pydantic import BaseModel, ConfigDict

from experiments.graph_retrieval.graph import ANY_RECEIPT, NAMES_RECIPIENT
from validator.classifier import TAXONOMY
from validator.llm import LLM, complete_json
from validator.models import ItemKind, RecipientType
from validator.policies import Rule
from validator.rules_config import RulesConfig

PROMPT_VERSION = "policy-graph-v1"
REFERENCE_KINDS = ("pre_approval", "vp_approval", "manager_approval", "baggage_delay")


class GraphReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triggered_by: list[str]
    restricted_to: list[str] = []
    overrides: list[str] = []
    replaces: list[str] = []
    takes_lines_from: list[str] = []
    waived_by: str | None = None


def fact_nodes(shared: RulesConfig) -> list[str]:
    """Every node a rule can be triggered by. From the taxonomy, the models and the shared tables."""
    return ([f"category:{c}" for c in sorted(TAXONOMY)] + [f"item_kind:{k}" for k in get_args(ItemKind)]
            + [f"recipient_type:{t}" for t in get_args(RecipientType)]
            + [f"expense_type:{e}" for e in sorted(shared.expense_type_covers)]
            + [f"mcc_risk:{m}" for m in ("high", "medium", "low")] + [NAMES_RECIPIENT, ANY_RECEIPT])


EXAMPLE = {
    "rule": {"id": "PARK-01", "title": "Airport parking", "text": "**PARK-01 — Airport parking.** Airport parking is "
             "reimbursable up to $20 a day for employees on a trip. Parking above the daily limit is a **low** exception "
             "unless a manager approval is recorded."},
    "edges": {"triggered_by": ["category:ground_transport"], "restricted_to": [], "overrides": [], "replaces": [],
              "takes_lines_from": [], "waived_by": "manager_approval"},
}


def system_prompt(shared: RulesConfig) -> str:
    return f"""You read one rule from a company's expense policies and list how it connects to receipts and to other rules, as the edges of a knowledge graph. A deterministic engine uses the graph to decide which rules to check on each receipt, so a missing edge means a rule is never checked.

Reply with one json object with exactly these keys:
- triggered_by: the receipt facts that make this rule worth checking. The engine checks the rule on any receipt that has at least one of them. Choose from this list only: {json.dumps(fact_nodes(shared), ensure_ascii=False)}. "{NAMES_RECIPIENT}" means a line names who the item is for. Use "{ANY_RECEIPT}" if the rule can apply to any receipt whatever it contains.
- restricted_to: regions whose employees alone the rule applies to, from {json.dumps(sorted(shared.regions))}. Empty if it applies everywhere.
- overrides: IDs of rules this rule replaces entirely wherever it applies (a regional rule replacing a global one).
- replaces: IDs of rules this rule may replace on a receipt where it is raised.
- takes_lines_from: IDs of rules this rule replaces only for the line items it covers.
- waived_by: the kind of recorded reference that satisfies the rule, one of {json.dumps(list(REFERENCE_KINDS))}, or null.

Use only rule IDs that appear in the text you are given. Here is a made-up example, not from these policies:
{json.dumps(EXAMPLE, ensure_ascii=False)}"""


def user_message(rule: Rule) -> str:
    return json.dumps({"rule": {"id": rule.id, "title": rule.title, "document": rule.doc, "section": rule.section,
                                "text": rule.text}}, ensure_ascii=False)


def problems(shared: RulesConfig, known_ids: set[str]):
    allowed, regions = set(fact_nodes(shared)), set(shared.regions)

    def check(reply: GraphReply) -> list[str]:
        found = []
        if not reply.triggered_by:
            found.append("triggered_by needs at least one fact")
        if unknown := set(reply.triggered_by) - allowed:
            found.append(f"triggered_by has nodes that are not allowed: {sorted(unknown)}")
        if unknown := set(reply.restricted_to) - regions:
            found.append(f"restricted_to has regions that are not allowed: {sorted(unknown)}")
        for field in ("overrides", "replaces", "takes_lines_from"):
            if unknown := set(getattr(reply, field)) - known_ids:
                found.append(f"{field} names rules that don't exist: {sorted(unknown)}")
        if reply.waived_by is not None and reply.waived_by not in REFERENCE_KINDS:
            found.append(f"waived_by must be one of {list(REFERENCE_KINDS)} or null")
        return found

    return check


def extract(llm: LLM, rule: Rule, shared: RulesConfig, known_ids: set[str]) -> dict:
    messages = [{"role": "system", "content": system_prompt(shared)}, {"role": "user", "content": user_message(rule)}]
    reply, _ = complete_json(llm, messages, GraphReply, purpose="policy_graph", prompt_version=PROMPT_VERSION,
                             check=problems(shared, known_ids), temperature=0, max_tokens=800)
    return reply.model_dump()
