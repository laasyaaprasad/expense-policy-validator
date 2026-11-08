"""Receipt detail: the line items with category and confidence, the exceptions with the policy text
they cite, and the correct answer alongside."""

import pandas as pd
import streamlit as st

from viewer import data

source = data.current_source()
results, gold, receipts, glosses = data.results(source), data.gold(), data.receipts(), data.glosses()
ids = sorted(results)

st.title("Receipt detail")
if st.button("Back to the receipt list", icon=":material/arrow_back:", type="tertiary"):
    st.switch_page("viewer/receipts.py")
selected = st.session_state.get("selected", ids[0])
selected = selected if selected in ids else ids[0]


def step(by: int) -> None:
    st.session_state["selected"] = ids[(ids.index(selected) + by) % len(ids)]


pick, previous, following = st.columns([6, 1, 1], vertical_alignment="bottom")
rid = pick.selectbox("Receipt", ids, index=ids.index(selected),
                     format_func=lambda r: f"{r} · {receipts[r].merchant_name} · {data.match_label(r, results[r])}")
previous.button("Previous", icon=":material/chevron_left:", on_click=step, args=(-1,), width="stretch")
following.button("Next", icon=":material/chevron_right:", on_click=step, args=(1,), width="stretch")
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
correct.metric("Correct answer", answer.disposition)
match = data.match_label(rid, record)
if match.endswith("(not scored)"):
    st.info(f"Out of scope on this track (timing rules), so not counted in the score. "
            f"{'It matches' if match.startswith('✓') else 'It differs from'} the correct answer.")
elif match == "✓":
    st.success("Matches the correct answer: the same disposition, exceptions, signals and line items cited.")
else:
    st.error("Differs from the correct answer in its disposition, its exceptions or signals, or the line items cited. "
             "Any wrong category is tinted below.")

result_tab, trace_tab = st.tabs([":material/fact_check: Result vs the correct answer",
                                 ":material/timeline: Trace: how it was decided"])

with result_tab:
    # --- line items ---------------------------------------------------------------------------------
    st.subheader("Line items")
    trace_lines = {x["line_id"]: x for x in (data.traces().get(rid) or {}).get("classify", {}).get("lines", [])} if source == data.DEFAULT_SOURCE else {}
    threshold = record.get("confidence_threshold") or 0.5

    def decided_by(line: dict) -> str:
        who = trace_lines.get(line["line_id"], {}).get("decided_by", "")
        return f"{who} → human review (EXC-03)" if who.startswith("model") and line["confidence"] < threshold else who

    lines = pd.DataFrame([{
        "line": line["line_id"],
        "text": line["description_raw"],
        **({"English gloss": glosses.get(line["line_id"], "")} if source == data.DEFAULT_SOURCE else {}),
        "category": line["category"],
        "correct category": answer.items[line["line_id"]].category,
        "restricted": line["restricted"],
        "confidence": line["confidence"],
        **({"decided by": decided_by(line)} if trace_lines else {}),
        "recipient": f"{line['recipient_name']} ({line['recipient_type']})" if line.get("recipient_name") else "",
        "reason": line.get("reason") or "",
    } for line in record["line_items"]])


    def tint(row: pd.Series) -> list[str]:
        return [f"background-color: {data.MISMATCH}" if row["category"] != row["correct category"] else ""] * len(row)


    st.dataframe(lines.style.apply(tint, axis=1).format({"confidence": "{:.3f}"}), hide_index=True, width="stretch")
    st.caption(f"Each line is decided by the semantic lookup only when every word matches, by meaning, a line a person reviewed and "
               f"no restricted line is close; otherwise by the model. A model confidence below {threshold} raises EXC-03 (human review) and keeps "
               "the item out of every rule. The lookup never decides a restricted category.")

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

with trace_tab:
    # --- the trace: how each stage got there ---------------------------------------------------------
    trace = data.traces().get(rid) if source == data.DEFAULT_SOURCE else None
    if trace is None:
        st.info("The stage-by-stage trace is recorded for the system's run only (outputs/traces.jsonl). "
                "Switch Results to System in the sidebar to see it.")
    else:
        engine = trace["engine"]
        translated = trace["translate"]["lines"] if trace.get("translate") else []
        no_call = trace["classify"]["call"] is None and all(x.get("decided_by", "").startswith("lookup") for x in trace["classify"]["lines"])
        st.markdown("**1 · Translate** (model). " + (
            f"Glossed into English: {', '.join(translated)}; the gloss is in the line items above." if translated
            else "Not needed: the lookup decided every line, so nothing was sent." if no_call
            else "Every line was already in English, so nothing was sent."))

        st.markdown("**2 · Classify** (semantic lookup first, then the model). " + (
            "The lookup decided every line, so the model was never called." if no_call
            else "Lines the lookup didn't decide went to the model, with the whole receipt; its confidence is the category's probability."))
        st.dataframe(pd.DataFrame([{
            "line": line["line_id"],
            "decided by": line.get("decided_by", "model"),
            "category": line["category"],
            "confidence": line["confidence"],
            "why": line.get("why", ""),
            "closest match": (lambda n: f"{n[0][0]} ({n[0][1]}, {n[0][2]:.3f})" if n else "")(line.get("nearest")),
            "runners-up (model)": ", ".join(f"{a['category']} {a['p']:.2g}" for a in line.get("alternatives", [])) if line.get("decided_by", "model").startswith("model") else "",
            "below threshold": "yes, human review (EXC-03)" if line["line_id"] in engine["uncertain_lines"] else "",
        } for line in trace["classify"]["lines"]]).style.format({"confidence": "{:.6f}"}), hide_index=True, width="stretch",
            column_config={"line": st.column_config.TextColumn(width=110), "decided by": st.column_config.TextColumn(width=130)})

        reasons = data.retrieval_reasons().get(rid, {})
        st.markdown(f"**3 · Retrieve** (code). {len(trace['retrieve']['rules'])} of the policy rules could apply, each for this reason:")
        st.dataframe(pd.DataFrame([{"rule": rule_id, "why it was retrieved": reasons.get(rule_id, "")}
                                   for rule_id in trace["retrieve"]["rules"]]), hide_index=True, width="stretch")

        st.markdown("**4 · Engine** (code). Each rule in the order it ran, and what its check found.")
        notes = []
        if engine["overridden_by_region"]:
            notes.append(f"Regional rules replace {', '.join(engine['overridden_by_region'])} for this employee's region, so those never ran.")
        for rule_id, lines in engine["lines_taken_over"].items():
            notes.append(f"{', '.join(lines)} were taken away from {rule_id} by a rule that covers them instead.")
        for name, total in engine["totals"].items():
            notes.append(f"Cross-receipt total `{name}`: " + ", ".join(f"{k} {v}" for k, v in total.items()))
        for note in notes:
            st.markdown(f"- {note}")

        def found(step: dict) -> str:
            if step["result"] == "replaced":
                return f"replaced by {step['by']}"
            if step["result"] == "not_evaluated":
                return step["reason"]
            if step["result"] == "waived":
                return f"waived by {step['reference']}"
            return ", ".join(f"{k}: {v}" for k, v in step.get("facts", {}).items())

        st.dataframe(pd.DataFrame([{
            "rule": rule_id,
            "result": step["result"],
            "severity": step.get("severity", ""),
            "lines": ", ".join(step.get("line_ids", [])) or ("whole receipt" if step["result"] == "raised" else ""),
            "what it found": found(step),
        } for rule_id, step in engine["rules"].items()]), hide_index=True, width="stretch")

        severities = sorted({step["severity"] for step in engine["rules"].values() if step["result"] == "raised"})
        has_signals = any(step["result"] == "signal" for step in engine["rules"].values())
        why = (f"an exception of {'/'.join(severities)} severity" if severities and set(severities) - {"low"}
               else "only low exceptions or review signals" if severities or has_signals else "no exceptions or signals")
        st.markdown(f"**5 · Disposition** (EXC-01): **{record['disposition']}**, from {why}. "
                    "Critical, high or medium means flag; only low exceptions or signals mean review; nothing means pass.")
