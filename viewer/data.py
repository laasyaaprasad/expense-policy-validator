"""What the viewer shows, read from committed outputs. The viewer makes no model calls.

The correct answers come from the answer key through evaluation/gold.py, like every other
evaluation; they are shown beside the results, never used to produce them.
"""

import ast
import inspect
import json

import streamlit as st

from evaluation.evaluate import BASELINE_PATH, METRICS_PATH, load_results, receipt_matches
from evaluation.gold import GoldReceipt, in_scope, load_gold
from validator.data import ROOT, ReceiptInput, load_receipts
from validator.pipeline import OUTPUT_DIR
from validator.rules_config import RulesConfig, load_rules

SOURCES = {"System": OUTPUT_DIR / "results.jsonl", "Keyword baseline": BASELINE_PATH}
DEFAULT_SOURCE = "System"
MISMATCH = "rgba(255, 80, 80, 0.22)"  # row tint that reads on light and dark themes


def current_source() -> str:
    """Chosen in the sidebar of app.py; a page run on its own shows the system."""
    return st.session_state.get("source", DEFAULT_SOURCE)


@st.cache_data
def results(source: str) -> dict[str, dict]:
    return load_results(SOURCES[source])


@st.cache_resource
def gold() -> dict[str, GoldReceipt]:
    return load_gold()


@st.cache_resource
def receipts() -> dict[str, ReceiptInput]:
    return {r.receipt_id: r for r in load_receipts()}


@st.cache_data
def glosses() -> dict[str, str]:
    with (OUTPUT_DIR / "classifications.jsonl").open(encoding="utf-8") as f:
        return {row["line_id"]: row["english"] for row in map(json.loads, f) if row["english"]}


@st.cache_data
def traces() -> dict[str, dict]:
    """The system's per-receipt trace: what each stage did (make run writes it)."""
    with (OUTPUT_DIR / "traces.jsonl").open(encoding="utf-8") as f:
        return {row["receipt_id"]: row for row in map(json.loads, f)}


@st.cache_data
def retrieval_reasons() -> dict[str, dict[str, str]]:
    """Rule ID → why the retriever chose it, per receipt."""
    with (OUTPUT_DIR / "retrieval.jsonl").open(encoding="utf-8") as f:
        return {row["receipt_id"]: row["reasons"] for row in map(json.loads, f)}


@st.cache_resource
def rules() -> RulesConfig:
    return load_rules()


@st.cache_data
def model_call(kind: str, key: str | None) -> dict | None:
    """One cached model call (cache/<kind>/<key>.json): exactly what was sent and what came back."""
    path = ROOT / "cache" / kind / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8")) if key and path.exists() else None


@st.cache_data
def tests_by_rule() -> dict[str, list[str]]:
    """Rule ID → the unit tests whose source mentions it, as "tests/<file>.py::<test>". The hand-written scenarios
    are listed by scenarios_by_rule."""
    ids = [rule.id for rule in load_rules().rules]
    found: dict[str, list[str]] = {rule_id: [] for rule_id in ids}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == "test_app.py":  # the viewer's own tests name rules as examples; they don't test them
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                body = ast.get_source_segment(source, node) or ""
                for rule_id in ids:
                    if rule_id in body:
                        found[rule_id].append(f"tests/{path.name}::{node.name}")
    return found


@st.cache_data
def scenarios_by_rule() -> dict[str, list[dict]]:
    """Rule ID → the hand-written scenarios that expect it (tests/fixtures/synthetic_receipts.jsonl), each as
    {"case", "expected"}, where expected reads like "flag: MEAL-01"."""
    ids = [rule.id for rule in load_rules().rules]
    found: dict[str, list[dict]] = {rule_id: [] for rule_id in ids}
    for row in (ROOT / "tests" / "fixtures" / "synthetic_receipts.jsonl").read_text(encoding="utf-8").splitlines():
        if not row.strip():
            continue
        scenario = json.loads(row)
        several = len(scenario["expected"]) > 1
        outcome = "; ".join(
            f"{rid + ' ' if several else ''}{e.get('disposition', '')}"
            + (f": {', '.join([*e.get('exceptions', {}), *e.get('signals', {})])}" if e.get("exceptions") or e.get("signals") else "")
            for rid, e in scenario["expected"].items())
        expected = json.dumps(scenario["expected"])
        for rule_id in ids:
            if f'"{rule_id}"' in expected:
                found[rule_id].append({"case": scenario["case"], "expected": outcome})
    return found


@st.cache_resource
def policies() -> dict:
    """Rule ID → the rule as written in the policy documents: its title, section heading, full text and the rules
    it mentions (validator/policies.py)."""
    from validator.policies import load_policies
    return load_policies()


@st.cache_data
def readings() -> dict[str, list[tuple[str, str]]]:
    """Rule ID → how the engine reads policy text that leaves room: (decision, policy sentence) rows from the table in
    NOTES.md. A row for "All caps" applies to every rule with a cap or threshold."""
    caps = [rule.id for rule in load_rules().rules if any("cap" in k or "threshold" in k for k in rule.params)]
    found: dict[str, list[tuple[str, str]]] = {}
    lines = (ROOT / "NOTES.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Rule | Decision | Policy sentence |"))
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rule_cell, decision, sentence = [cell.strip() for cell in line.strip("|").split("|")][:3]
        targets = caps if rule_cell == "All caps" else [r.strip() for r in rule_cell.split(",")]
        for rule_id in targets:
            found.setdefault(rule_id, []).append((decision, sentence))
    return found


def check_source(rule_id: str) -> str | None:
    """The Python that checks a rule: its registered check, with the module's helpers it calls, or for an engine
    mechanic the code that carries it out."""
    from validator.engine import context, disposition, engine, rules as checks
    mechanics = {"EXC-01": [disposition.disposition], "EXC-02": [checks._needs_reference],
                 "EXC-03": [context.ReceiptView.__init__], "EXC-04": [engine._check_evidence],
                 "MCC-03": [context.ReceiptView.on_premises.fget], "REG-05": [context.ReceiptView.rate.fget]}
    if rule_id in mechanics:
        return "\n\n".join(inspect.getsource(fn) for fn in mechanics[rule_id])
    fn = checks.CHECKS.get(rule_id)
    if fn is None:
        return None
    source = inspect.getsource(fn)
    helpers = [name for name, obj in vars(checks).items()
               if name.startswith("_") and inspect.isfunction(obj) and f"{name}(" in source]
    return "\n\n".join([source] + [inspect.getsource(getattr(checks, name)) for name in helpers])


@st.cache_data
def rule_texts() -> dict[str, str]:
    """Rule ID → the policy's own words for it (the `evidence` quote in rules/rules.yaml)."""
    return {rule.id: " ".join(rule.evidence.split()) for rule in load_rules().rules}


@st.cache_data
def metrics() -> dict:
    return json.loads(METRICS_PATH.read_text(encoding="utf-8"))


@st.cache_data
def cord_metrics() -> dict:
    """The frozen system on 100 real CORD receipts (`cord/README.md`), as committed."""
    return json.loads((ROOT / "outputs" / "cord" / "metrics.json").read_text(encoding="utf-8"))


@st.cache_data
def evaluation_section(heading: str) -> str:
    """The body of one `## ` section of docs/evaluation.md, the written answers to Part 3."""
    text = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
    body = text.split(f"\n## {heading}\n", 1)[1]
    return body.split("\n## ", 1)[0].strip()


def match_label(receipt_id: str, record: dict) -> str:
    """✓ or ✗ against the correct answer; the out-of-scope timing receipts say so and never count as mismatches."""
    mark = "✓" if receipt_matches(record, gold()[receipt_id]) else "✗"
    return mark if in_scope(receipt_id) else f"{mark} (not scored)"


def lookup_in_shadow() -> bool:
    """Whether the committed run had the semantic lookup in shadow mode (it records answers in `shadow`, decides none)."""
    return any(line.get("shadow") for trace in traces().values() for line in trace["classify"]["lines"])
