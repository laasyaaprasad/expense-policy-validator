"""Receipt list: every receipt, our disposition beside the correct one, mismatches tinted."""

import pandas as pd
import streamlit as st

from viewer import data

source = data.current_source()
results, gold, receipts = data.results(source), data.gold(), data.receipts()

st.title("Receipts")

def findings(exceptions: list[str], signals: list[str]) -> str:
    """Exception rule IDs, then any review signals in brackets, in one cell."""
    return " ".join(part for part in (", ".join(exceptions), signals and f"({', '.join(signals)})") if part)


rows = []
for rid in sorted(results):
    record, answer = results[rid], gold[rid]
    rows.append({
        "receipt": rid,
        "match": data.match_label(rid, record),
        "ours": record["disposition"],
        "correct": answer.disposition,
        "merchant": receipts[rid].merchant_name,
        "expense type": receipts[rid].submitted_expense_type,
        "our findings": findings([e["rule_id"] for e in record["exceptions"]], [s["rule_id"] for s in record["review_signals"]]),
        "correct findings": findings([e.rule_id for e in answer.exceptions], [s.rule_id for s in answer.review_signals]),
    })
table = pd.DataFrame(rows)
wrong = int((table["match"] == "✗").sum())
unscored = int(table["match"].str.endswith("(not scored)").sum())
scored = len(table) - unscored

right, mismatched, out = st.columns(3)
right.metric(f"Exactly right ({source})", f"{scored - wrong} / {scored}")
mismatched.metric("Mismatches", wrong)
out.metric("Not scored (timing rules)", unscored)

st.caption(
    "✗ (tinted) means the receipt differs from the correct answer: a wrong disposition, a missing or extra exception "
    "or signal, or wrong line items cited. The four timing receipts are out of scope on this track: they show whether "
    "they match, but don't count toward the score. Findings list the exceptions raised, with review signals in brackets. "
    "**Click a row to open the receipt.**"
)
if st.toggle("Show only mismatches"):
    table = table[table["match"] == "✗"].reset_index(drop=True)
    if table.empty:
        st.success(f"No mismatches: every scored receipt matches the correct answer ({source}).")
        st.stop()


def tint(row: pd.Series) -> list[str]:
    return [f"background-color: {data.MISMATCH}" if row["match"] == "✗" else ""] * len(row)


widths = {"receipt": 75, "match": 110, "ours": 50, "correct": 60, "merchant": 175, "expense type": 145,
          "our findings": 195, "correct findings": 195}
narrow = {name: st.column_config.TextColumn(width=width) for name, width in widths.items()}
event = st.dataframe(table.style.apply(tint, axis=1), hide_index=True, width="stretch", height=35 * (len(table) + 1) + 3,
                     column_config=narrow, on_select="rerun", selection_mode="single-row", key="receipt_table")
if event.selection.rows:
    st.session_state["selected"] = table.iloc[event.selection.rows[0]]["receipt"]
    st.switch_page("viewer/detail.py")
