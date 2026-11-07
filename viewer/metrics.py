"""Metrics: the Part 3 numbers in one place, from outputs/metrics.json (`make eval`)."""

from collections import Counter

import pandas as pd
import streamlit as st

from viewer import data

m = data.metrics()
systems = {"System": m, **({"Keyword baseline": m["baseline"]} if "baseline" in m else {})}


def rate(r: dict) -> str:
    return f"{r['k']} / {r['n']}  ({r['rate']:.0%}; 95% CI {r['ci95'][0]:.0%}–{r['ci95'][1]:.0%})"


st.title("Metrics")
st.caption(f"On the 28 in-scope receipts. {m['note']} "
           f"Confidence threshold {m['confidence_threshold']}. Intervals are 95% Wilson intervals.")

st.subheader("Classification (65 line items)")
who = Counter(line.get("decided_by", "model") for trace in data.traces().values() for line in trace["classify"]["lines"])
calls = sum(trace["classify"]["call"] is not None for trace in data.traces().values())
st.caption(f"Who decided the {sum(who.values())} lines of the 32 receipts: " + ", ".join(f"{k} {v}" for k, v in sorted(who.items(), key=lambda kv: -kv[1]))
           + f". The model was called for {calls} of 32 receipts; the semantic lookup decided the rest of the lines (validator/lookup.py).")
st.dataframe(pd.DataFrame({name: {
    "macro F1": f"{s['items']['macro_f1']:.3f} over {s['items']['categories']} categories",
    "restricted F1": f"{s['items']['restricted_f1']:.3f}",
    "items right": rate(s["items"]["accuracy"]),
} for name, s in systems.items()}), width="stretch")
st.markdown(
    "Restricted F1 is the input the engine needs most: a missed restricted item is a missed violation, a false one "
    "a false flag. Macro F1 also covers the unrestricted categories that drive caps and scoping (food for the "
    "meal caps, computer accessories for TECH-02), and with 7 one-item categories each error moves it about 5 points."
)
with st.expander("Per category"):
    per = {name: {c["category"]: c for c in s["items"]["per_category"]} for name, s in systems.items()}
    st.dataframe(pd.DataFrame([{"category": cat, "items": row["support"],
                                **{f"F1 · {name}": per[name][cat]["f1"] for name in systems}}
                               for cat, row in per["System"].items()]).style.format(precision=2),
                 hide_index=True, width="stretch")

st.subheader("Disposition (28 receipts)")
columns = st.columns(len(systems))
for column, (name, s) in zip(columns, systems.items()):
    d = s["disposition"]
    with column:
        st.markdown(f"**{name}**: rows are the correct answer, columns ours")
        st.dataframe(pd.DataFrame(d["matrix"], index=[f"correct {x}" for x in d["labels"]], columns=d["labels"]), width="stretch")
        st.markdown(f"Exactly right: {rate(s['exact_match'])}  \n"
                    f"False positives (of the receipts that should pass): {rate(d['false_positives'])}  \n"
                    f"Missed violations (of the receipts that should be flagged): {rate(d['missed_violations'])}")

st.subheader("Exceptions")
st.dataframe(pd.DataFrame({name: {
    "found": f"{s['exceptions']['matched']} of {s['exceptions']['gold']}",
    "raised": str(s["exceptions"]["raised"]),  # a string, like the column's other cells, so Arrow takes it as is
    "evidence exactly right": f"{s['exceptions']['evidence_correct']['k']} of {s['exceptions']['evidence_correct']['n']}",
} for name, s in systems.items()}), width="stretch")
with st.expander("Per rule"):
    rules = {name: {r["rule_id"]: r for r in s["exceptions"]["per_rule"]} for name, s in systems.items()}
    names = sorted(set().union(*rules.values()))

    def pr(r: dict | None) -> str:
        if r is None:
            return ""
        fmt = lambda x: "n/a" if x is None else f"{x:.2f}"
        return f"P {fmt(r['precision'])} · R {fmt(r['recall'])}"

    st.dataframe(pd.DataFrame([{"rule": rule, "in correct answers": next(r[rule]["gold"] for r in rules.values() if rule in r),
                                **{name: pr(rules[name].get(rule)) for name in systems}} for rule in names]),
                 hide_index=True, width="stretch")

st.subheader("Failures and where they come from")
if m["blame"]["mismatched"]:
    st.dataframe(pd.DataFrame(m["blame"]["receipts"]), hide_index=True, width="stretch")
else:
    st.markdown("The system gets none of the 28 in-scope receipts wrong, so the blame report is empty. "
                "The four timing receipts are not scored on this track; the timing rules built after the frozen run get them right:")
st.dataframe(pd.DataFrame([{**o, "got_rules": ", ".join(o["got_rules"]), "want_rules": ", ".join(o["want_rules"])}
                           for o in m["out_of_scope"]]), hide_index=True, width="stretch")
with st.expander("Two failure cases, by stage (docs/evaluation.md)"):
    st.markdown(data.evaluation_section("6. Two failure cases"))

st.subheader("The base-rate question")
st.markdown(data.evaluation_section("7. The base-rate question"))

st.subheader("Out-of-sample check: 100 real receipts (CORD v2)")
cord = data.cord_metrics()
lines, sure, r = cord["items"]["all"], cord["items"]["sure"], cord["receipts"]
st.caption("The submitted system, on 100 real Indonesian receipts it never saw (CORD v2, CC-BY-4.0). "
           "Caveats: the assistant wrote these labels and only 57 were hand-checked, after the results were known; "
           "only one line is restricted; and every receipt has the same made-up context, so the regional, "
           "cross-receipt and timing rules never come into play. Full method in cord/README.md.")
tiles = [*st.columns(2), *st.columns(2)]  # two per row, so the values aren't cut off
tiles[0].metric("Lines right", f"{lines['category_correct']['k']} / {lines['category_correct']['n']}",
                help="Lines whose label is unsure (packaging, freebies) are included.")
tiles[1].metric("Lines with a sure label", f"{sure['category_correct']['k']} / {sure['category_correct']['n']}")
tiles[2].metric("Violations caught", f"{r['flag_precision']['k']} / {r['missed_flags']['n']}")
tiles[3].metric("False alarms", f"{r['false_alarms']['k']} / {r['false_alarms']['n']}",
                help=f"{r['false_flags']['k']} flagged and {r['false_reviews']['k']} sent to review, of the clean receipts.")
st.markdown(f"So {r['flag_precision']['k']} of its {r['flag_precision']['n']} flags were right. Every false flag turns on a line "
            "whose label is itself unsure; if those labels went the system's way, all "
            f"{cord['receipts_if_unsure_labels_follow_system']['flag_precision']['k']} flags would be right.")
with st.expander(f"The {len(r['disagreements'])} receipts where we disagree, and the {len(cord['item_disagreements'])} lines behind them"):
    st.dataframe(pd.DataFrame([{"receipt": d["receipt_id"], "ours": d["ours"], "label says": d["want"],
                                "our findings": ", ".join(d["our_rules"])} for d in r["disagreements"]]),
                 hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame([{"line": d["line_id"], "text": d["description_raw"], "English": d["english"],
                                "ours": d["ours"], "label": d["label"], "label sure": "yes" if d["sure"] else "no",
                                "confidence": d["confidence"], "label note": d.get("label_note") or ""}
                               for d in cord["item_disagreements"]]).style.format({"confidence": "{:.3f}"}),
                 hide_index=True, width="stretch")

st.subheader("Confidence threshold sweep")
st.caption(m["threshold_sweep"]["note"] + ". In use: " + str(m["confidence_threshold"]) + ".")
st.dataframe(pd.DataFrame(m["threshold_sweep"]["rows"]), hide_index=True, width="stretch")

st.subheader("Cost")
u = m["usage"]
st.markdown(f"Per receipt: {u['per_receipt']['prompt_tokens']:.0f} prompt tokens ({u['per_receipt']['prompt_cache_hit_tokens']:.0f} "
            f"from the provider's prompt cache) and {u['per_receipt']['completion_tokens']:.0f} completion tokens, "
            f"${u['per_receipt']['usd']:.5f} as billed. "
            f"Oracle mode (engine on the correct categories): {m['oracle']['k']} / {m['oracle']['n']} exactly right.")
