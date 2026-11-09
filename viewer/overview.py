"""Overview: the goal, the approach in three lines, the headline numbers and where to look next."""

import streamlit as st
from streamlit.errors import StreamlitPageNotFoundError

from viewer import data

m, cord = data.metrics(), data.cord_metrics()
d, items, base = m["disposition"], m["items"], m["baseline"]

st.title("Expense Policy Validator")
st.markdown(
    "**The goal.** Northwind's audit team gets thousands of itemized expense receipts. For each one, decide **pass, flag "
    "or review** against nine written policies: give every line a category, and raise exceptions that name the rule, "
    "cite the lines and quote the policy. Catch the real violations without burying reviewers in false alarms, which "
    "is where the old keyword rules failed.")

st.markdown(
    "**The approach.**\n"
    "- **AI reads the receipt.** It only says what each line is (wine, a monitor, a gift card), in any of six languages, "
    "with a confidence. A lookup reuses answers people already approved, so repeats skip the AI.\n"
    "- **Plain code applies the policy.** The nine documents became 40 rules (`rules/rules.yaml`), each checked by a "
    "small, tested Python function. Same input, same verdict, every time.\n"
    "- **Everything traces back.** Each exception quotes the policy word for word, and each receipt keeps a trace of "
    "every step, down to what the model was sent.")

st.subheader("Headline numbers (28 scored receipts)")
tiles = st.columns(4)
tiles[0].metric("Receipts exactly right", f"{d['accuracy']['k']} / {d['accuracy']['n']}",
                f"keyword baseline {base['exact_match']['k']} / {base['exact_match']['n']}", delta_color="off", delta_arrow="off")
tiles[1].metric("Line items right", f"{items['accuracy']['k']} / {items['accuracy']['n']}",
                f"keyword baseline {base['items']['accuracy']['k']} / {base['items']['accuracy']['n']}", delta_color="off", delta_arrow="off")
tiles[2].metric("False alarms", f"{d['false_positives']['k']} / {d['false_positives']['n']}",
                help="Receipts that should pass but were flagged or sent to review.")
tiles[3].metric("Missed violations", f"{d['missed_violations']['k']} / {d['missed_violations']['n']}",
                help="Receipts that should be flagged but were passed or sent to review.")
lines = cord["items"]["all"]["category_correct"]
cost = m["usage"]["classify"]["usd"] + m["usage"]["translate"]["usd"]
st.caption(
    f"With only 9 clean receipts, 0 false alarms still fits a real rate up to "
    f"{d['false_positives']['ci95'][1]:.0%} (95% interval), so this proves less than it looks. On 100 real "
    f"Indonesian receipts it never saw, it got {lines['k']} of {lines['n']} lines right. A full run of the 32 receipts "
    f"costs about \\${cost:.4f} in model calls. The Metrics page has the rest.")

st.subheader("Where to look")
LINKS = [
    ("viewer/architecture.py", "Architecture: the pipeline, step by step", ":material/account_tree:"),
    ("viewer/receipts.py", "Receipt list: every receipt against the correct answer, searchable", ":material/list:"),
    ("viewer/detail.py", "Receipt detail: one receipt's verdict, evidence and full trace", ":material/receipt_long:"),
    ("viewer/rules.py", "Rulebook: every rule, its code and its tests", ":material/gavel:"),
    ("viewer/metrics.py", "Metrics: the evaluation, the baseline and the base-rate question", ":material/analytics:"),
    ("viewer/review.py", "Review: the lines a person should check, which teach the lookup", ":material/rate_review:"),
    ("viewer/aws.py", "AWS deployment: the design on paper and what it costs", ":material/cloud:"),
]
for path, label, icon in LINKS:
    try:
        st.page_link(path, label=label, icon=icon)
    except StreamlitPageNotFoundError:  # the page run on its own, outside app.py's navigation
        st.markdown(f"- {label}")
