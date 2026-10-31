"""Receipt detail: the line items with category and confidence, the exceptions with the policy text
they cite, and the correct answer alongside."""

import pandas as pd
import streamlit as st

from viewer import data

source = data.current_source()
results, gold, receipts, glosses = data.results(source), data.gold(), data.receipts(), data.glosses()
ids = sorted(results)

st.title("Receipt detail")
selected = st.session_state.get("selected", ids[0])
rid = st.selectbox("Receipt", ids, index=ids.index(selected) if selected in ids else 0,
                   format_func=lambda r: f"{r} · {receipts[r].merchant_name} · {data.match_label(r, results[r])}")
st.session_state["selected"] = rid
record, answer, receipt = results[rid], gold[rid], receipts[rid]

# --- the receipt --------------------------------------------------------------------------------
left, middle, right = st.columns(3)
left.markdown(f"**{receipt.merchant_name}**  \nMCC {receipt.mcc} · {receipt.mcc_description}  \n"
              f"{receipt.transaction_datetime:%Y-%m-%d %H:%M} (local)")
middle.markdown(f"**{receipt.total_amount:,.2f} {receipt.currency}** (${receipt.total_amount_usd:,.2f})  \n"
                f"Declared: {receipt.submitted_expense_type}  \n"
                f"Attendees: {receipt.attendee_count if receipt.attendee_count is not None else 'not recorded'}")
right.markdown(f"Employee {receipt.employee_id} · {receipt.employee_country} ({receipt.entity})  \n"
               f"Trip: {receipt.trip_id or 'none'}  \nReference: {receipt.pre_approval_ref or 'none'}")

ours, correct = st.columns(2)
ours.metric(f"Ours ({source})", record["disposition"])
correct.metric("Correct answer", answer.disposition,
               delta=None if data.match_label(rid, record) == "not scored" else
               ("match" if data.match_label(rid, record) == "✓" else "mismatch"),
               delta_color="normal" if data.match_label(rid, record) == "✓" else "inverse")
if data.match_label(rid, record) == "not scored":
    st.info("Out of scope on this track (timing rules), so not scored.")

# --- line items ---------------------------------------------------------------------------------
st.subheader("Line items")
lines = pd.DataFrame([{
    "line": line["line_id"],
    "text": line["description_raw"],
    **({"English gloss": glosses.get(line["line_id"], "")} if source == data.DEFAULT_SOURCE else {}),
    "category": line["category"],
    "correct category": answer.items[line["line_id"]].category,
    "restricted": line["restricted"],
    "confidence": line["confidence"],
    "recipient": f"{line['recipient_name']} ({line['recipient_type']})" if line.get("recipient_name") else "",
    "reason": line.get("reason") or "",
} for line in record["line_items"]])


def tint(row: pd.Series) -> list[str]:
    return [f"background-color: {data.MISMATCH}" if row["category"] != row["correct category"] else ""] * len(row)


st.dataframe(lines.style.apply(tint, axis=1).format({"confidence": "{:.3f}"}), hide_index=True, width="stretch")
st.caption(f"Confidence below {record.get('confidence_threshold') or 0.5} raises EXC-03 and keeps the item out of every rule.")

# --- exceptions and signals ---------------------------------------------------------------------
gold_lines = {e.rule_id: sorted(e.line_ids) for e in answer.exceptions}
our_lines = {e["rule_id"]: sorted(e["line_ids"]) for e in record["exceptions"]}
ours, correct = st.columns(2)
with ours:
    st.subheader("Exceptions raised")
    if not record["exceptions"]:
        st.write("None.")
    for e in record["exceptions"]:
        verdict = ("✓ in the correct answer" if gold_lines.get(e["rule_id"]) == sorted(e["line_ids"]) else
                   "≈ in the correct answer, other line items" if e["rule_id"] in gold_lines else "✗ not in the correct answer")
        with st.container(border=True):
            st.markdown(f"**{e['rule_id']}** · {e['severity']} · {', '.join(e['line_ids']) or 'whole receipt'}  \n{verdict}")
            st.write(e["explanation"])
            st.markdown("\n".join(f"> {line}" for line in e["evidence"].splitlines()))
            st.caption(e["policy_doc"])
    st.subheader("Review signals")
    for s in record["review_signals"]:
        st.markdown(f"**{s['rule_id']}**: {s['reason']}")
    if not record["review_signals"]:
        st.write("None.")
with correct:
    st.subheader("Correct exceptions")
    if not answer.exceptions:
        st.write("None.")
    for e in answer.exceptions:
        found = "✓ raised" if our_lines.get(e.rule_id) == sorted(e.line_ids) else "≈ raised, other line items" if e.rule_id in our_lines else "✗ missed"
        with st.container(border=True):
            st.markdown(f"**{e.rule_id}** · {e.severity} · {', '.join(e.line_ids) or 'whole receipt'}  \n{found}")
            st.write(e.reason)
    st.subheader("Correct signals")
    for s in answer.review_signals:
        st.markdown(f"**{s.rule_id}**: {s.reason}")
    if not answer.review_signals:
        st.write("None.")

with st.expander("Waivers, rules not evaluated, and every rule considered"):
    for w in record.get("waived", []):
        st.markdown(f"Waived **{w['rule_id']}**: {w.get('explanation', w['reference'])}")
    for n in record.get("not_evaluated", []):
        st.markdown(f"Not evaluated **{n['rule_id']}**: {n['reason']}")
    st.write("Rules evaluated: " + ", ".join(record.get("rules_evaluated", [])))
    st.caption(f"policy {record.get('policy_version')} · {record.get('provider')} / {record.get('model')} · {record.get('prompt_version')}")
