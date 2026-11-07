"""Results viewer (Part 5). `make app`. It reads committed outputs only and makes no model calls."""

import streamlit as st

from viewer.data import DEFAULT_SOURCE, SOURCES

st.set_page_config(page_title="Expense Policy Validator", layout="wide")
page = st.navigation([
    st.Page("viewer/receipts.py", title="Receipt list", icon=":material/list:", default=True),
    st.Page("viewer/detail.py", title="Receipt detail", icon=":material/receipt_long:"),
    st.Page("viewer/metrics.py", title="Metrics", icon=":material/analytics:"),
])
# The choice lives in a plain session key, not the widget's own: st.switch_page reruns without the
# sidebar's widget state, and the radio would fall back to its default.
st.session_state.setdefault("source", DEFAULT_SOURCE)
if page.title == "Metrics":
    st.sidebar.caption("Metrics show the system and the keyword baseline side by side.")
else:
    st.sidebar.radio("Results", list(SOURCES), index=list(SOURCES).index(st.session_state["source"]), key="source_choice",
                     on_change=lambda: st.session_state.update(source=st.session_state["source_choice"]),
                     help="The system's results, or the keyword baseline through the same engine.")
page.run()
