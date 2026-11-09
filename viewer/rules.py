"""Rulebook: every rule in rules/rules.yaml, what it says, when it is retrieved, where it fired against the correct
answer, the Python that checks it and the tests that cover it."""

import pandas as pd
import streamlit as st

from viewer import data

source = data.current_source()
results, gold = data.results(source), data.gold()
config = data.rules()
specs = {rule.id: rule for rule in config.rules}
retrieved = data.retrieval_reasons() if source == data.DEFAULT_SOURCE else {}

st.title("Rulebook")
st.caption(f"The {len(specs)} rules in `rules/rules.yaml`, drafted once from the nine policy documents and checked word for "
           "word against them. The numbers and the policy text live in the YAML; each rule that can fire has a small Python "
           "check that reads them. **Click a row to see one rule in full.**")


def md(text: str) -> str:
    return text.replace("$", r"\$")


def when(rule) -> str:
    """When the retriever selects the rule, in words."""
    a = rule.applies_to
    parts = (["every receipt"] if a.always else []) + [f"category {', '.join(a.categories)}"] * bool(a.categories) \
        + [f"item kind {', '.join(a.item_kinds)}"] * bool(a.item_kinds) \
        + [f"recipient {', '.join(a.recipient_types)}"] * bool(a.recipient_types) \
        + ["a line names its recipient"] * a.names_recipient \
        + [f"expense type {', '.join(a.expense_types)}"] * bool(a.expense_types) \
        + [f"{', '.join(a.mcc_risk)}-risk MCC"] * bool(a.mcc_risk)
    text = " or ".join(parts) or ("always, built into the engine" if rule.kind == "mechanic" else "")
    return f"{text} ({', '.join(a.regions)} only)" if a.regions else text


def fired(rule_id: str) -> tuple[list[str], list[str]]:
    """The receipts where we raised the rule, and where the correct answer has it."""
    ours = [rid for rid, r in results.items() if any(f["rule_id"] == rule_id for f in r["exceptions"] + r["review_signals"])]
    right = [rid for rid, g in gold.items() if any(f.rule_id == rule_id for f in (*g.exceptions, *g.review_signals))]
    return ours, right


rows = []
for rule in config.rules:
    ours, right = fired(rule.id)
    rows.append({
        "rule": rule.id,
        "document": rule.doc.removesuffix(".md"),
        "kind": rule.kind.replace("_", " "),
        "severity": (rule.severity if isinstance(rule.severity, str) else
                     ", ".join(f"{k}: {v}" for k, v in rule.severity.items()) if rule.severity else ""),
        "retrieved when": when(rule),
        "raised by us": len(ours),
        "in the correct answer": len(right),
        "what the rule says": " ".join(rule.evidence.split()),
    })
table = pd.DataFrame(rows)

search, doc = st.columns([3, 2])
query = search.text_input("Search", placeholder="Rule ID or words, e.g. ALC, alcohol, carve-out, baggage")
docs = doc.multiselect("Policy document", sorted(table["document"].unique()))
shown = table
if query:
    words = query.lower().split()
    haystack = shown.astype(str).agg(" ".join, axis=1).str.lower()
    shown = shown[haystack.apply(lambda text: all(word in text for word in words))]
if docs:
    shown = shown[shown["document"].isin(docs)]
shown = shown.reset_index(drop=True)

event = st.dataframe(shown, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="rule_table",
                     column_config={"rule": st.column_config.TextColumn(width=80),
                                    "raised by us": st.column_config.NumberColumn(width=90),
                                    "in the correct answer": st.column_config.NumberColumn(width=120),
                                    "what the rule says": st.column_config.TextColumn(width="large")})
if event.selection.rows:
    st.session_state["rule"] = shown.iloc[event.selection.rows[0]]["rule"]

ids = list(specs)
current = st.session_state.get("rule", ids[0])
rule_id = st.selectbox("Rule", ids, index=ids.index(current if current in specs else ids[0]),
                       format_func=lambda r: f"{r} · {' '.join(specs[r].evidence.split())[:90]}…")
st.session_state["rule"] = rule_id
rule = specs[rule_id]

# --- one rule in full -----------------------------------------------------------------------------
with st.container(border=True):
    severity = rule.severity if isinstance(rule.severity, str) else \
        ", ".join(f"{k}: {v}" for k, v in rule.severity.items()) if rule.severity else "none"
    st.markdown(f"### {rule.id}\n{rule.kind.replace('_', ' ')} · severity **{severity}** · "
                f"{'about ' + ('the whole receipt' if rule.scope == 'receipt' else 'line items') if rule.scope else ''} · "
                f"`{rule.doc}`")
    st.markdown("\n".join(f"> {md(line)}" for line in rule.evidence.splitlines()))

    facts = [f"**Retrieved when:** {when(rule)}"]
    if rule.params:
        facts.append("**Numbers:** " + ", ".join(f"`{k}` = {v}" for k, v in rule.params.items()))
    if rule.requires_reference:
        facts.append(f"**Excused by:** a recorded `{rule.requires_reference}` reference (EXC-02); any other kind waives nothing")
    if rule.overrides:
        facts.append(f"**Overrides:** {', '.join(rule.overrides)}, wherever this rule applies")
    if rule.line_overrides:
        facts.append(f"**Takes over from:** {', '.join(rule.line_overrides)}, on the lines it cites")
    if rule.replaces:
        facts.append(f"**May replace:** {', '.join(rule.replaces)}, on a receipt where it is raised")
    overridden_by = [other.id for other in config.rules if rule.id in other.overrides]
    if overridden_by:
        facts.append(f"**Overridden by:** {', '.join(overridden_by)}, for that region's employees")
    st.markdown("  \n".join(facts))

ours, right = fired(rule_id)
picked = [rid for rid, reasons in retrieved.items() if rule_id in reasons]
st.subheader("Where it fired")
if rule.kind == "mechanic":
    st.write("A mechanic applies to every receipt as part of the engine, so it is never retrieved or raised on its own.")
else:
    # Receipts where it was raised or expected first, then those that only retrieved it.
    where = sorted(set(ours) | set(right) | set(picked), key=lambda rid: (rid not in ours and rid not in right, rid))
    if not where:
        st.write("Never retrieved, raised or expected on these receipts.")
    else:
        fired_table = pd.DataFrame([{
            "receipt": rid,
            "retrieved": "yes" if rid in picked else ("" if source == data.DEFAULT_SOURCE else "not traced"),
            "why it was retrieved": retrieved.get(rid, {}).get(rule_id, ""),
            "raised by us": "yes" if rid in ours else "",
            "in the correct answer": "yes" if rid in right else "",
            "agree": "✓" if (rid in ours) == (rid in right) else "✗",
        } for rid in where])
        st.caption(f"{len(picked)} receipt(s) retrieved it, we raised it on {len(ours)}, and the correct answer has it on "
                   f"{len(right)}. **Click a row to open the receipt.**")
        hit = st.dataframe(fired_table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                           key=f"fired-{rule_id}")
        if hit.selection.rows:
            st.session_state["selected"] = fired_table.iloc[hit.selection.rows[0]]["receipt"]
            st.switch_page("viewer/detail.py")

st.subheader("How it's checked")
code = data.check_source(rule_id)
if code:
    st.caption("The Python behind the rule, with the helpers it calls, from `validator/engine/`. It reads its numbers from "
               "the YAML above. Checks never call each other; the engine runs them in a fixed order.")
    st.code(code, language="python")
else:
    st.write("No check of its own.")

st.subheader("Tests that cover it")
tests = data.tests_by_rule().get(rule_id, [])
st.markdown("\n".join(f"- `{t}`" for t in tests) if tests else "No test names this rule directly.")
if rule.kind != "mechanic":
    st.caption("Every check is also covered by `tests/test_check_coverage.py`, which switches each one off in turn and "
               "expects a test to fail.")
