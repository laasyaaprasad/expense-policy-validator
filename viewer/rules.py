"""Rulebook: one rule at a time, from the policy's own words to how we read it, what it does to the verdict, how well
it works, the Python that checks it and the tests that cover it. Every rule is in the table at the bottom."""

import pandas as pd
import streamlit as st

from viewer import data

source = data.current_source()
results, gold = data.results(source), data.gold()
config, policies, metrics = data.rules(), data.policies(), data.metrics()
specs = {rule.id: rule for rule in config.rules}
retrieved = data.retrieval_reasons() if source == data.DEFAULT_SOURCE else {}
FLAG = {"critical", "high", "medium"}


def md(text: str) -> str:
    return text.replace("$", r"\$")


def severity_text(rule) -> str:
    if isinstance(rule.severity, dict):
        return ", ".join(f"{k}: {v}" for k, v in rule.severity.items())
    return rule.severity or ""


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


def on_its_own(rule) -> str | None:
    """The verdict this rule gives when it is the only finding (EXC-01)."""
    if rule.kind == "mechanic":
        return None
    if rule.kind == "review_signal":
        return "**review**: a signal is not a breach, so on its own it sends the receipt to a person"
    if isinstance(rule.severity, dict):
        return " · ".join(f"**{'flag' if v in FLAG else 'review'}** for {k} ({v})" for k, v in rule.severity.items())
    return f"**{'flag' if rule.severity in FLAG else 'review'}** ({rule.severity} severity)"


MECHANIC_ORDER = {
    "EXC-01": "Last: it turns every finding into the verdict.",
    "EXC-02": "Inside each check that asks for a reference: the right kind of reference waives the rule.",
    "EXC-03": "Before any check: unsure lines are left out of every rule, then raised as one signal at the end.",
    "EXC-04": "After each check: an item rule must cite its lines, a whole-receipt rule none.",
    "MCC-03": "Inside the alcohol checks: a bar counts as on the premises, never retail.",
    "REG-05": "Inside every check that converts money: the receipt's own rate on its date.",
}
checks_in_order = sorted([r for r in config.rules if r.kind != "mechanic" and not r.overrides],
                         key=lambda r: not (r.line_overrides or r.replaces))


def runs(rule) -> str:
    """Where the rule sits in the engine's fixed order (validator/engine/engine.py)."""
    if rule.kind == "mechanic":
        return MECHANIC_ORDER.get(rule.id, "Built into the engine.")
    if rule.overrides:
        return (f"Before any check runs: for its region's employees it switches off {', '.join(rule.overrides)}, then "
                "runs as a check itself.")
    if rule.line_overrides or rule.replaces:
        return (f"Among the first checks, because it can take over from {', '.join(rule.line_overrides or rule.replaces)}. "
                "No rule triggers another.")
    return (f"With the other checks, number {checks_in_order.index(rule) + 1} of {len(checks_in_order)} in "
            "`rules.yaml` order. No rule triggers another.")


def fired(rule_id: str) -> tuple[list[str], list[str]]:
    """The receipts where we raised the rule, and where the correct answer has it."""
    ours = [rid for rid, r in results.items() if any(f["rule_id"] == rule_id for f in r["exceptions"] + r["review_signals"])]
    right = [rid for rid, g in gold.items() if any(f.rule_id == rule_id for f in (*g.exceptions, *g.review_signals))]
    return ours, right


def accuracy(block: dict, rule_id: str) -> dict | None:
    rows = block["exceptions"]["per_rule"] + block["signals"]["per_rule"]
    return next((row for row in rows if row["rule_id"] == rule_id), None)


def open_receipt(receipt_id: str) -> None:
    st.session_state["selected"] = receipt_id


# --- header ----------------------------------------------------------------------------------------
st.title("Rulebook")
st.caption(f"The {len(specs)} rules in `rules/rules.yaml`, drafted once from the nine policy documents and checked word "
           "for word against them. Pick a rule to see where it comes from, how we read it, what it does to the verdict, "
           "how well it works and how it's checked. Every rule is in the table at the bottom.")
with st.expander("How to read this page", icon=":material/help:"):
    st.markdown(
        "- **Exception:** a breach of policy. Its severity sets the verdict: any **critical, high or medium** exception "
        "means **flag**; **low** ones on their own mean **review** (EXC-01).\n"
        "- **Review signal:** not a breach, just worth a look. On its own it means **review**.\n"
        "- **Mechanic:** how the engine itself works (EXC-01 to 04, MCC-03, REG-05). Always on, never retrieved.\n"
        "- **Retrieved when:** the tag the retriever matches against the receipt; any one match is enough, and a "
        "regional rule must also match the employee's region.\n"
        "- **Accuracy** is on the 28 scored receipts, against the answer key.\n"
        "- TIME-04 is the one policy rule not encoded: it needs trip dates, which no trip record here has.")

ids = list(specs)
current = st.session_state.get("rule", ids[0])
rule_id = st.selectbox("Rule", ids, index=ids.index(current if current in specs else ids[0]),
                       format_func=lambda r: f"{r} · {policies[r].title if r in policies else ''}")
st.session_state["rule"] = rule_id
rule, written = specs[rule_id], policies.get(rule_id)

# --- the rule in full ------------------------------------------------------------------------------
with st.container(border=True):
    st.markdown(f"### {rule.id} · {written.title if written else ''}\n"
                f"{rule.kind.replace('_', ' ')}" + (f" · severity **{severity_text(rule)}**" if rule.severity else "")
                + (f" · about {'the whole receipt' if rule.scope == 'receipt' else 'line items'}" if rule.scope else "")
                + f" · `{rule.doc}`")
    quote, original = st.columns(2, gap="large")
    with quote:
        st.markdown("**What we quote** (stored word for word in `rules.yaml`, and on every finding)")
        st.markdown("\n".join(f"> {md(line)}" for line in rule.evidence.splitlines()))
    with original:
        st.markdown(f"**The policy in full** ({written.section if written and written.section else rule.doc})")
        if written:
            with st.container(height=220):
                st.markdown(md(written.text))

    st.markdown("**Effect on the verdict.** "
                + (f"On its own it gives {on_its_own(rule)}. " if on_its_own(rule) else "")
                + f"**Where it runs:** {runs(rule)}")

    facts = [f"**Retrieved when:** {when(rule)}"]
    if rule.params:
        facts.append("**Numbers:** " + ", ".join(f"`{k}` = {v}" for k, v in rule.params.items()))
    if rule.requires_reference:
        facts.append(f"**Excused by:** a recorded `{rule.requires_reference}` reference (EXC-02); any other kind waives nothing")
    links = []
    if written and written.mentions:
        links.append(f"cites {', '.join(sorted(written.mentions))}")
    cited_by = sorted(other for other, w in policies.items() if rule_id in w.mentions and other in specs)
    if cited_by:
        links.append(f"cited by {', '.join(cited_by)}")
    if rule.overrides:
        links.append(f"overrides {', '.join(rule.overrides)} for its region")
    if rule.line_overrides:
        links.append(f"takes over from {', '.join(rule.line_overrides)} on the lines it cites")
    if rule.replaces:
        links.append(f"may replace {', '.join(rule.replaces)} on a receipt where it is raised")
    overridden_by = [other.id for other in config.rules if rule_id in other.overrides]
    if overridden_by:
        links.append(f"overridden by {', '.join(overridden_by)} for that region's employees")
    if links:
        facts.append("**Links to other rules:** " + "; ".join(links))
    st.markdown("  \n".join(facts))

    reading = data.readings().get(rule_id, [])
    if reading:
        st.markdown("**How we read it** (where the policy leaves room, from the readings table in `NOTES.md`; each has a "
                    "test)\n" + "\n".join(f"- {md(decision)}" for decision, _ in reading))

# --- how well it works ----------------------------------------------------------------------------
if rule.kind != "mechanic":
    st.subheader("How well it works")
    ours_acc, base_acc = accuracy(metrics, rule_id), accuracy(metrics["baseline"], rule_id)
    if ours_acc is None and base_acc is None:
        st.write("Neither expected nor raised on the 28 scored receipts, so there is nothing to score.")
    else:
        row = ours_acc or {"gold": 0, "raised": 0, "matched": 0, "evidence_correct": 0, "precision": None, "recall": None}
        pct = lambda v: "—" if v is None else f"{v:.0%}"
        tiles = st.columns(5)
        tiles[0].metric("Expected (answer key)", row["gold"])
        tiles[1].metric("Raised by us", row["raised"])
        tiles[2].metric("Precision", pct(row["precision"]), help="Of the times we raised it, how often the answer key has it.")
        tiles[3].metric("Recall", pct(row["recall"]), help="Of the times the answer key has it, how often we raised it.")
        tiles[4].metric("Right lines cited", f"{row['evidence_correct']} / {row['matched']}",
                        help="Of the matches, how many cite exactly the right line items.")
        if base_acc:
            st.caption(f"The keyword baseline, through the same engine: raised {base_acc['raised']}, matched "
                       f"{base_acc['matched']} of {base_acc['gold']} (precision {pct(base_acc['precision'])}, recall "
                       f"{pct(base_acc['recall'])}).")

# --- a real example -------------------------------------------------------------------------------
ours, right = fired(rule_id)
example = next(((rid, f) for rid in sorted(ours)
                for f in results[rid]["exceptions"] + results[rid]["review_signals"] if f["rule_id"] == rule_id), None)
if example:
    rid, finding = example
    st.subheader("What a finding looks like")
    with st.container(border=True):
        st.markdown(f"**{rid}** · {finding.get('severity', 'signal')} · "
                    f"{', '.join(finding.get('line_ids', [])) or 'whole receipt'}  \n"
                    f"{md(finding.get('explanation') or finding.get('reason', ''))}")
        if st.button(f"Open {rid}", key=f"example-{rid}", icon=":material/receipt_long:", type="tertiary",
                     on_click=open_receipt, args=(rid,)):
            st.switch_page("viewer/detail.py")

# --- where it fired -------------------------------------------------------------------------------
picked = [rid for rid, reasons in retrieved.items() if rule_id in reasons]
if rule.kind != "mechanic":
    st.subheader("Where it fired")
    # Receipts where it was raised or expected first, then those that only retrieved it.
    where = sorted(set(ours) | set(right) | set(picked), key=lambda rid: (rid not in ours and rid not in right, rid))
    if not where:
        st.write("Never retrieved, raised or expected on these receipts.")
    else:
        fired_table = pd.DataFrame([{
            "receipt": rid,
            "raised by us": "yes" if rid in ours else "",
            "in the correct answer": "yes" if rid in right else "",
            "agree": "✓" if (rid in ours) == (rid in right) else "✗",
            "retrieved": "yes" if rid in picked else ("" if source == data.DEFAULT_SOURCE else "not traced"),
            "why it was retrieved": retrieved.get(rid, {}).get(rule_id, ""),
        } for rid in where])
        st.caption(f"{len(picked)} receipt(s) retrieved it, we raised it on {len(ours)}, and the correct answer has it on "
                   f"{len(right)}. **Click a row to open the receipt.**")
        hit = st.dataframe(fired_table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                           key=f"fired-{rule_id}")
        if hit.selection.rows:
            st.session_state["selected"] = fired_table.iloc[hit.selection.rows[0]]["receipt"]
            st.switch_page("viewer/detail.py")

# --- how it's checked and tested ------------------------------------------------------------------
st.subheader("How it's checked")
code = data.check_source(rule_id)
if code:
    st.caption("The Python behind the rule, with the helpers it calls, from `validator/engine/`. It reads its numbers from "
               "`rules.yaml`. Checks never call each other; the engine runs them in a fixed order.")
    st.code(code, language="python")
else:
    st.write("No check of its own.")

st.subheader("Tests that cover it")
scenarios = data.scenarios_by_rule().get(rule_id, [])
tests = data.tests_by_rule().get(rule_id, [])
if scenarios:
    st.markdown("**Hand-written scenarios** (`tests/fixtures/synthetic_receipts.jsonl`): each is a small receipt and the "
                "verdict it must get.")
    st.dataframe(pd.DataFrame(scenarios).rename(columns={"case": "scenario", "expected": "must give"}),
                 hide_index=True, width="stretch")
if tests:
    st.markdown("**Unit tests that name it**\n" + "\n".join(
        f"- {t.split('::')[1].removeprefix('test_').replace('_', ' ')} <small>(`{t.split('::')[0]}`)</small>"
        for t in tests), unsafe_allow_html=True)
if not scenarios and not tests:
    st.write("No test names this rule directly.")
if rule.kind != "mechanic":
    st.caption("Every check is also covered by `tests/test_check_coverage.py`, which switches each one off in turn and "
               "expects a test to fail.")

# --- every rule -----------------------------------------------------------------------------------
st.divider()
st.subheader(f"All {len(specs)} rules")
rows = []
for r in config.rules:
    raised, expected = fired(r.id)
    rows.append({
        "rule": r.id,
        "title": policies[r.id].title if r.id in policies else "",
        "document": r.doc.removesuffix(".md"),
        "kind": r.kind.replace("_", " "),
        "severity": severity_text(r),
        "retrieved when": when(r),
        "raised by us": len(raised),
        "in the correct answer": len(expected),
        "what the rule says": " ".join(r.evidence.split()),
    })
table = pd.DataFrame(rows)
search, doc, kind = st.columns([3, 2, 2])
query = search.text_input("Search", placeholder="Rule ID or words, e.g. ALC, alcohol, carve-out, baggage")
docs = doc.multiselect("Policy document", sorted(table["document"].unique()))
kinds = kind.multiselect("Kind", sorted(table["kind"].unique()))
shown = table
if query:
    words = query.lower().split()
    haystack = shown.astype(str).agg(" ".join, axis=1).str.lower()
    shown = shown[haystack.apply(lambda text: all(word in text for word in words))]
if docs:
    shown = shown[shown["document"].isin(docs)]
if kinds:
    shown = shown[shown["kind"].isin(kinds)]
shown = shown.reset_index(drop=True)
st.caption("**Click a row to open that rule at the top of the page.**")
version = st.session_state.setdefault("rule_table_version", 0)
event = st.dataframe(shown, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                     key=f"rule_table_{version}",
                     column_config={"rule": st.column_config.TextColumn(width=80),
                                    "raised by us": st.column_config.NumberColumn(width=90),
                                    "in the correct answer": st.column_config.NumberColumn(width=120),
                                    "what the rule says": st.column_config.TextColumn(width="large")})
if event.selection.rows:
    st.session_state["rule"] = shown.iloc[event.selection.rows[0]]["rule"]
    st.session_state["rule_table_version"] = version + 1  # a fresh table, so the old selection doesn't stick
    st.rerun()
