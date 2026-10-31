"""Receipt list: every receipt, our disposition beside the correct one, mismatches tinted."""

import pandas as pd
import streamlit as st

from viewer import data

source = data.current_source()
results, gold, receipts = data.results(source), data.gold(), data.receipts()

st.title("Receipts")

rows = []
for rid in sorted(results):
    record, answer = results[rid], gold[rid]
    rows.append({
        "receipt": rid,
        "merchant": receipts[rid].merchant_name,
        "expense type": receipts[rid].submitted_expense_type,
        "ours": record["disposition"],
        "correct": answer.disposition,
        "match": data.match_label(rid, record),
        "our exceptions": ", ".join(e["rule_id"] for e in record["exceptions"]),
        "correct exceptions": ", ".join(e.rule_id for e in answer.exceptions),
        "our signals": ", ".join(s["rule_id"] for s in record["review_signals"]),
        "correct signals": ", ".join(s.rule_id for s in answer.review_signals),
    })
table = pd.DataFrame(rows)
wrong = int((table["match"] == "✗").sum())

st.caption(
    f"**{source}.** {wrong} of 28 in-scope receipts differ from the correct answer (✗, tinted): a wrong disposition, "
    "a missing or extra exception or signal, or wrong line items cited. The four timing receipts are out of scope "
    "and not scored. Select a row to open it."
)


def tint(row: pd.Series) -> list[str]:
    return [f"background-color: {data.MISMATCH}" if row["match"] == "✗" else ""] * len(row)


event = st.dataframe(table.style.apply(tint, axis=1), hide_index=True, width="stretch", height=35 * 33 + 3,
                     on_select="rerun", selection_mode="single-row", key="receipt_table")
if event.selection.rows:
    st.session_state["selected"] = table.iloc[event.selection.rows[0]]["receipt"]
    st.switch_page("viewer/detail.py")
