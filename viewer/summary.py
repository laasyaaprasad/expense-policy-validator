"""The last page: the headline numbers against the keyword baseline, what the project cost, and the stretch goals."""

import streamlit as st

from evaluation.gold import in_scope
from viewer import data

m = data.metrics()
d, items, exc, base = m["disposition"], m["items"], m["exceptions"], m["baseline"]


def kn(x: dict) -> str:
    return f"{x['k']} / {x['n']}"


st.title("Summary")
st.markdown(
    "**The keyword baseline** is the kind of filter this replaces: a list of restricted words (wine, beer, gift card…) "
    "labels each line instead of the AI, and the same engine decides. So every gap below is the AI's reading alone.")
left, right = st.columns([3, 2])
left.markdown(
    "| On the 28 scored receipts | This system | Keyword baseline |\n|---|---|---|\n"
    f"| Receipts exactly right | **{kn(d['accuracy'])}** | {kn(base['exact_match'])} |\n"
    f"| Line items right | **{kn(items['accuracy'])}** | {kn(base['items']['accuracy'])} |\n"
    f"| False alarms (should pass) | **{kn(d['false_positives'])}** | {kn(base['disposition']['false_positives'])} |\n"
    f"| Missed violations (should flag) | **{kn(d['missed_violations'])}** | {kn(base['disposition']['missed_violations'])} |\n"
    f"| Exceptions found, of {exc['gold']} | **{exc['matched']}** | {base['exceptions']['matched']} |")
spend = data.spend()
calls, usd = spend["total"]
with right:
    st.metric("Cost of a run on the 28 scored receipts", f"${data.run_cost([r for r in sorted(data.traces()) if in_scope(r)]):.4f}",
              help="Model calls only, as billed. All 32 receipts: "
                   f"${data.run_cost(sorted(data.traces())):.4f}. Everything else runs on a laptop.")
    st.metric("Total spend on this project", f"${usd:.2f}", f"{calls:,} model calls", delta_color="off", delta_arrow="off",
              help=" · ".join(f"{k}: ${v[1]:.4f} over {v[0]:,} calls" for k, v in spend.items() if k != "total")
              + ". Includes every experiment, the dev set, CORD and smoke tests; not the coding assistant.")

st.subheader("Stretch goals")
per_rule, base_rules = exc["per_rule"], base["exceptions"]["per_rule"]
base_perfect = sum(r["precision"] == 1 and r["recall"] == 1 for r in base_rules)
st.markdown(
    "- **The four timing receipts: all four match.** TIME-01 (bought overnight), TIME-02 (a meal with no trip) and "
    "TIME-03 (a split across receipts) were written from the policy text and tested on hand-written cases before "
    "rescoring. Not an independent result: their answers were known.\n"
    f"- **Precision and recall per rule: 1.00 on all {len(per_rule)} rules in the answers** "
    f"(the baseline is perfect on {base_perfect}).\n"
    f"- **Exact lines cited: {kn(exc['evidence_correct'])}** matched exceptions "
    f"(the baseline {kn(base['exceptions']['evidence_correct'])}).\n"
    "- **Left out: TIME-04 (weekend spend on a weekday trip).** It sends a weekend purchase made outside the trip's "
    "start and end dates to review, and the policy says it applies only when the trip record has those dates. The "
    "data gives each trip only an ID and a number of nights, no dates, so it could never fire, and a check that "
    "can't fire can't be tested. It is listed as `out_of_scope` in `rules.yaml` rather than coded.\n"
    "- **Not done:** TIME-03 only groups receipts with the same merchant name on the same date, so a split across "
    "two names or two days is missed.")
