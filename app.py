"""Results viewer (Part 5). `make app`. It reads committed outputs and makes no model calls; the Review page only appends
people's reviews to the lookup's reviews.jsonl, which `make learn` applies."""

import streamlit as st

from viewer.data import DEFAULT_SOURCE, SOURCES

st.set_page_config(page_title="Expense Policy Validator", layout="wide")
page = st.navigation({
    "Start here": [
        st.Page("viewer/overview.py", title="Overview and goal", icon=":material/flag:", default=True),
        st.Page("viewer/architecture.py", title="Architecture", icon=":material/account_tree:"),
    ],
    "Results": [
        st.Page("viewer/receipts.py", title="Receipt list", icon=":material/list:"),
        st.Page("viewer/detail.py", title="Receipt detail", icon=":material/receipt_long:"),
        st.Page("viewer/rules.py", title="Rulebook", icon=":material/gavel:"),
        st.Page("viewer/metrics.py", title="Metrics", icon=":material/analytics:"),
        st.Page("viewer/review.py", title="Review", icon=":material/rate_review:"),
    ],
    "Production": [
        st.Page("viewer/aws.py", title="AWS deployment and cost", icon=":material/cloud:"),
    ],
})
# The choice lives in a plain session key, not the widget's own: st.switch_page reruns without the
# sidebar's widget state, and the radio would fall back to its default.
st.session_state.setdefault("source", DEFAULT_SOURCE)
if page.title in ("Receipt list", "Receipt detail", "Rulebook"):
    st.sidebar.radio("Results", list(SOURCES), index=list(SOURCES).index(st.session_state["source"]), key="source_choice",
                     on_change=lambda: st.session_state.update(source=st.session_state["source_choice"]),
                     help="The system's results, or the keyword baseline through the same engine.")
elif page.title == "Metrics":
    st.sidebar.caption("Metrics show the system and the keyword baseline side by side.")
elif page.title == "Review":
    st.sidebar.caption("Reviews are saved for `make learn`; they change no result until then.")
page.run()
