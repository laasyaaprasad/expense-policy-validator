"""Receipt detail: the line items with category and confidence, the exceptions with the policy text
they cite, and the correct answer alongside; and the trace, every pipeline step in the architecture diagram's order."""

import json

import pandas as pd
import streamlit as st

from validator.normaliser import normalise
from validator.reviews import review_queue
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
rate = (f"  \nRate: 1 {receipt.currency} = {receipt.fx_rate_to_usd:,.6g} USD"
        if receipt.fx_rate_to_usd is not None and receipt.currency not in (None, "USD") else "")
middle.markdown(f"**{receipt.total_amount:,.2f} {receipt.currency}** (${receipt.total_amount_usd:,.2f}){rate}  \n"
                f"Declared: {receipt.submitted_expense_type}  \n"
                f"Attendees: {receipt.attendee_count if receipt.attendee_count is not None else 'not recorded'}")
nights = f" ({receipt.trip_nights} nights)" if receipt.trip_id and receipt.trip_nights is not None else ""
right.markdown(f"Employee {receipt.employee_id} · {receipt.employee_country} ({receipt.entity})  \n"
               f"Trip: {receipt.trip_id or 'none'}{nights}  \nReference: {receipt.pre_approval_ref or 'none'}")

def md(text: str) -> str:
    """Markdown-safe text: a pair of dollar signs would otherwise render as a formula."""
    return text.replace("$", r"\$")


# --- the verdict at a glance --------------------------------------------------------------------
texts_by_line = {item.line_id: item.description_raw for item in receipt.line_items}
with st.container(border=True):
    n_exc, n_sig = len(record["exceptions"]), len(record["review_signals"])
    headline = {"flag": "Flagged", "review": "Sent for review", "pass": "Passes"}[record["disposition"]]
    st.markdown(f"#### {headline}" + (f" · {n_exc} exception(s)" if n_exc else "") + (f" · {n_sig} signal(s)" if n_sig else "")
                + f" <small>({source}; correct answer: {answer.disposition})</small>", unsafe_allow_html=True)
    bullets = [f"- **{e['rule_id']}** ({e['severity']}) · "
               + (", ".join(f"{line_id.rsplit('-', 1)[-1]} “{md(texts_by_line.get(line_id, ''))}”" for line_id in e["line_ids"])
                  or "whole receipt") + f": {md(e['explanation'])}" for e in record["exceptions"]]
    bullets += [f"- **{s['rule_id']}** (signal): {md(s['reason'])}" for s in record["review_signals"]]
    st.markdown("\n".join(bullets) if bullets else "Nothing breaks policy, and nothing needs a person.")
    st.caption(f"What this receipt tests (the answer key's note; the system never sees it): {md(answer.notes)}")

match = data.match_label(rid, record)
if match.endswith("(not scored)"):
    st.info(f"Out of scope on this track (timing rules), so not counted in the score. "
            f"{'It matches' if match.startswith('✓') else 'It differs from'} the correct answer.")
elif match == "✓":
    st.success("Matches the correct answer: the same disposition, exceptions, signals and line items cited.")
else:
    st.error("Differs from the correct answer in its disposition, its exceptions or signals, or the line items cited. "
             "Any wrong category is tinted below.")


def open_rule(rule_id: str) -> None:
    st.session_state["rule"] = rule_id


result_tab, trace_tab = st.tabs([":material/fact_check: Result vs the correct answer",
                                 ":material/timeline: Trace: how it was decided"])

with result_tab:
    # --- exceptions and signals, beside the correct answer ---------------------------------------------
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
                st.markdown(md(e["explanation"]))
                st.markdown("\n".join(f"> {md(line)}" for line in e["evidence"].splitlines()))
                st.caption(e["policy_doc"])
                if st.button(f"Open {e['rule_id']} in the Rulebook", key=f"rule-{e['rule_id']}", icon=":material/gavel:",
                             type="tertiary", on_click=open_rule, args=(e["rule_id"],)):
                    st.switch_page("viewer/rules.py")
        st.subheader("Review signals")
        for s in record["review_signals"]:
            st.markdown(f"**{s['rule_id']}**: {md(s['reason'])}")
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
                st.markdown(md(e.reason))
        st.subheader("Correct signals")
        for s in answer.review_signals:
            st.markdown(f"**{s.rule_id}**: {md(s.reason)}")
        if not answer.review_signals:
            st.write("None.")

    # --- line items ---------------------------------------------------------------------------------
    st.subheader("Line items")
    trace_lines = {x["line_id"]: x for x in (data.traces().get(rid) or {}).get("classify", {}).get("lines", [])} if source == data.DEFAULT_SOURCE else {}
    threshold = record.get("confidence_threshold") or 0.5

    def decided_by(line: dict) -> str:
        t = trace_lines.get(line["line_id"], {})
        who = t.get("decided_by", "") + (f" (lookup in shadow: {t['shadow']})" if t.get("shadow") else "") + (" · audit sample" if t.get("audit") else "")
        return f"{who} → human review (EXC-03)" if who.startswith("model") and line["confidence"] < threshold else who

    cited: dict[str, list[str]] = {}
    for finding in record["exceptions"] + record["review_signals"]:
        for line_id in finding.get("line_ids", []):
            cited.setdefault(line_id, []).append(finding["rule_id"])
    money = {item.line_id: item for item in receipt.line_items}
    currency = receipt.currency or ""
    lines = pd.DataFrame([{
        "line": line["line_id"],
        "text": line["description_raw"],
        **({"English gloss": glosses.get(line["line_id"], "")} if source == data.DEFAULT_SOURCE else {}),
        "cited by": ", ".join(cited.get(line["line_id"], [])),
        "category": line["category"],
        "correct category": answer.items[line["line_id"]].category,
        "restricted": line["restricted"],
        "confidence": line["confidence"],
        "qty": float(money[line["line_id"]].quantity),
        f"unit price ({currency})": float(money[line["line_id"]].unit_price),
        f"amount ({currency})": float(money[line["line_id"]].amount),
        "amount ($)": float(money[line["line_id"]].amount_usd),
        **({"decided by": decided_by(line)} if trace_lines else {}),
        "recipient": f"{line['recipient_name']} ({line['recipient_type']})" if line.get("recipient_name") else "",
        "reason": line.get("reason") or "",
    } for line in record["line_items"]])


    def tint(row: pd.Series) -> list[str]:
        return [f"background-color: {data.MISMATCH}" if row["category"] != row["correct category"] else ""] * len(row)


    st.dataframe(lines.style.apply(tint, axis=1).format({
        "confidence": "{:.3f}", "qty": "{:g}", f"unit price ({currency})": "{:,.2f}", f"amount ({currency})": "{:,.2f}",
        "amount ($)": "{:,.2f}"}), hide_index=True, width="stretch")
    st.caption("*Cited by* names the exceptions and signals that cite a line; a whole-receipt exception cites none. "
               + ("The semantic lookup runs in shadow mode: it records what it would decide, and the model decides every line. "
                  if data.lookup_in_shadow() else
                  "Each line is decided by the semantic lookup when every word matches, by meaning, a line a person reviewed and no "
                  "restricted line is close; otherwise by the model. A fixed 2% of the lookup's decisions are marked as an audit "
                  "sample for a person to check. ")
               + f"A model confidence below {threshold} raises EXC-03 (human review) and keeps the item out of every rule. "
               "The lookup never decides a restricted category.")

    with st.expander("Waivers, rules not evaluated, and every rule considered"):
        for w in record.get("waived", []):
            st.markdown(f"Waived **{w['rule_id']}**: {md(w.get('explanation', w['reference']))}")
        for n in record.get("not_evaluated", []):
            st.markdown(f"Not evaluated **{n['rule_id']}**: {md(n['reason'])}")
        st.write("Rules evaluated: " + ", ".join(record.get("rules_evaluated", [])))
        st.caption(f"policy {record.get('policy_version')} · {record.get('provider')} / {record.get('model')} · {record.get('prompt_version')}")

with trace_tab:
    # --- the trace: every step in the order the pipeline runs it, as in the README's architecture diagram -------
    trace = data.traces().get(rid) if source == data.DEFAULT_SOURCE else None
    if trace is None:
        st.info("The step-by-step trace is recorded for the system's run only (outputs/traces.jsonl). "
                "Switch Results to System in the sidebar to see it.")
    else:
        engine, trace_lines_all = trace["engine"], trace["classify"]["lines"]
        texts = {line.line_id: line.description_raw for line in receipt.line_items}
        in_shadow = data.lookup_in_shadow()
        who = lambda x: x.get("decided_by", "model")
        closest = lambda x: (lambda n: f"{n[0][0]} ({n[0][1]}, {n[0][2]:.3f})" if n else "")(x.get("nearest"))
        st.caption("Every step in the order the pipeline runs it, as in the architecture diagram: normalise, lookup, translate, "
                   "classify, retrieve, engine, report and routing.")

        def pretty(text: str) -> str:
            try:
                return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except (TypeError, ValueError):
                return text

        def show_call(kind: str, key: str | None, what: str) -> None:
            """Exactly what one model call was sent and returned, from the committed cache (cache/<kind>/<key>.json)."""
            call = data.model_call(kind, key)
            if call is None:
                return
            params, response = call["request"]["params"], call["response"]
            usage = response.get("usage") or {}
            messages = {m["role"]: m["content"] for m in params["messages"]}
            reasoning = (params.get("extra_body") or {}).get("reasoning", {}).get("enabled")
            with st.expander(f"What the model was sent and what it returned: the {what} call", icon=":material/data_object:"):
                st.markdown(
                    f"`{params['model']}` served by **{response.get('provider')}** · temperature {params.get('temperature')} · "
                    f"reasoning {'on' if reasoning else 'off'} · prompt `{call['request']['prompt_version']}` · "
                    f"{usage.get('prompt_tokens')} tokens in ({(usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0} "
                    f"cached), {usage.get('completion_tokens')} out · \\${usage.get('cost') or 0:.6f} · `cache/{kind}/{key}.json`")
                sent, returned = st.columns(2)
                sent.markdown("**Sent**: only the merchant, merchant category, declared expense type and line text; "
                              "no amounts, dates or employee details.")
                sent.code(pretty(messages.get("user", "")), language="json")
                returned.markdown("**Returned**" + (": confidence is the probability of each category's tokens, which are "
                                                    "stored in the same file." if params.get("logprobs") else ""))
                returned.code(pretty(response.get("content", "")), language="json")
                st.markdown("**Instructions** (the system prompt, the same for every receipt)")
                st.code(messages.get("system", ""), language="markdown", height=240)

        # 1 · Normalise -----------------------------------------------------------------------------------------
        cleaned = {line_id: normalise(text) for line_id, text in texts.items()}
        changed = [line_id for line_id in cleaned if cleaned[line_id] != texts[line_id]]

        def code_check(x: dict) -> str:
            if who(x) == "code (not itemized)":
                return "not itemized: the receipt shows only a total, so code records the line as other"
            if who(x) == "model (code check)":
                return f"straight to the model: {x.get('why', '')}"
            return "passed, on to the lookup"

        st.markdown("**1 · Normalise** (code). Each line's text is cleaned up (full-width characters folded, spaces collapsed); "
                    "then code checks send a line straight to the model if it may name a recipient, may be a phone, prescription "
                    "or event ticket, has no real words, or comes from a merchant where context decides. "
                    + (f"Cleaned: {', '.join(changed)}." if changed else "Nothing needed cleaning on this receipt."))
        st.dataframe(pd.DataFrame([{
            "line": x["line_id"],
            "text": texts.get(x["line_id"], ""),
            **({"after clean-up": cleaned.get(x["line_id"], "")} if changed else {}),
            "code check": code_check(x),
        } for x in trace_lines_all]), hide_index=True, width="stretch")

        # 2 · Lookup --------------------------------------------------------------------------------------------
        reached = [x for x in trace_lines_all if who(x) in ("model", "lookup (memory)")]
        by_lookup = [x for x in reached if who(x).startswith("lookup")]
        st.markdown("**2 · Lookup: seen it before?** (semantic lookup). A line is decided here only when every word matches, by "
                    "meaning, a line a person reviewed, and no restricted line is close; it never decides a restricted category. " + (
                        "No line reached the lookup." if not reached
                        else "It runs in shadow mode: it only records what it would decide, and every line goes on to the model."
                        if in_shadow
                        else f"It decided {len(by_lookup)} of {len(reached)} lines; the rest go on to the model."))
        if reached:
            st.dataframe(pd.DataFrame([{
                "line": x["line_id"],
                "result": (f"would decide: {x['shadow']}" if x.get("shadow") else "not seen before, to the model") if in_shadow
                          else f"seen before: {x['category']}" if who(x).startswith("lookup") else "not seen before, to the model",
                "why": x.get("why", ""),
                "closest reviewed line": closest(x),
                "audit sample": "yes, a person spot-checks it" if x.get("audit") else "",
            } for x in reached]), hide_index=True, width="stretch")

        # 3 · Translate -----------------------------------------------------------------------------------------
        translated = trace["translate"]["lines"] if trace.get("translate") else []
        no_call = trace["classify"]["call"] is None
        st.markdown("**3 · Translate** (model). " + (
            "Lines the lookup didn't decide that aren't in English are glossed into English for the classifier." if translated
            else "Not needed: no line went to the model." if no_call
            else "Not needed: every line the model reads is already in English."))
        if translated:
            st.dataframe(pd.DataFrame([{"line": line_id, "text": texts.get(line_id, ""), "English gloss": glosses.get(line_id, "")}
                                       for line_id in translated]), hide_index=True, width="stretch")
            show_call("translate", trace["translate"].get("call"), "translation")

        # 4 · Classify ------------------------------------------------------------------------------------------
        model_rows = []
        for x in trace_lines_all:
            if who(x).startswith("model"):
                model_rows.append({"line": x["line_id"], "model's answer": x["category"], "confidence": x["confidence"], "used as": "the decision",
                                   "runners-up": ", ".join(f"{a['category']} {a['p']:.2g}" for a in x.get("alternatives", [])),
                                   "below 0.5": "yes, to a person (EXC-03)" if x["line_id"] in engine["uncertain_lines"] else ""})
            elif x.get("model_said"):
                model_rows.append({"line": x["line_id"], "model's answer": x["model_said"][0], "confidence": x["model_said"][1],
                                   "used as": "a second opinion on the lookup", "runners-up": "", "below 0.5": ""})
        st.markdown("**4 · Classify** (model, temperature 0). " + (
            "The model wasn't called: " + ("the receipt isn't itemized." if any(who(x) == "code (not itemized)" for x in trace_lines_all)
                                           else "the lookup decided every line.") if no_call
            else "The model reads the whole receipt and names each line's category; its confidence is the category's probability. "
                 "For lines the lookup decided, its answer is kept as a second opinion."))
        if model_rows:
            st.dataframe(pd.DataFrame(model_rows).style.format({"confidence": "{:.6f}"}), hide_index=True, width="stretch")
        show_call("classify", trace["classify"].get("call"), "classification")

        # 5 · Retrieve ------------------------------------------------------------------------------------------
        reasons = data.retrieval_reasons().get(rid, {})
        rule_text = data.rule_texts()
        st.markdown(f"**5 · Retrieve** (code). {len(trace['retrieve']['rules'])} of the policy rules could apply, each for this "
                    "reason. Click a cell to read the whole rule.")
        st.dataframe(pd.DataFrame([{"rule": rule_id, "why it was retrieved": reasons.get(rule_id, ""),
                                    "what the rule says": rule_text.get(rule_id, "")}
                                   for rule_id in trace["retrieve"]["rules"]]), hide_index=True, width="stretch",
                     column_config={"rule": st.column_config.TextColumn(width="small"),
                                    "why it was retrieved": st.column_config.TextColumn(width="medium"),
                                    "what the rule says": st.column_config.TextColumn(width="large")})

        # 6 · Engine --------------------------------------------------------------------------------------------
        st.markdown("**6 · Engine** (code). Each rule in the order it ran, and what its check found.")
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

        # 7 · Report --------------------------------------------------------------------------------------------
        severities = sorted({step["severity"] for step in engine["rules"].values() if step["result"] == "raised"})
        has_signals = any(step["result"] == "signal" for step in engine["rules"].values())
        why = (f"an exception of {'/'.join(severities)} severity" if severities and set(severities) - {"low"}
               else "only low exceptions or review signals" if severities or has_signals else "no exceptions or signals")
        st.markdown(f"**7 · Report** (EXC-01). Disposition **{record['disposition']}**, from {why}. "
                    "Critical, high or medium means flag; only low exceptions or signals mean review; nothing means pass. "
                    f"{len(record['exceptions'])} exception(s) and {len(record['review_signals'])} signal(s), each with the policy "
                    "text it quotes, are on the Result tab.")

        # 8 · Routing -------------------------------------------------------------------------------------------
        routed = review_queue([trace], [record], texts, every_line=True)
        queued = [q for q in routed if q["reasons"]]
        verdict = record["disposition"]
        whole = ([f"{e['rule_id']} ({e['severity']})" for e in record["exceptions"] if not e.get("line_ids")]
                 + [f"{s['rule_id']} (signal)" for s in record["review_signals"] if not s.get("line_ids")])
        st.markdown("**8 · Routing** (code). Two things are routed: the receipt's verdict, and each line's category.")
        st.markdown(
            ("- **The receipt** passes, so no one needs to approve it." if verdict == "pass" else
             f"- **The receipt** is **{'flagged' if verdict == 'flag' else 'sent for review'}**, so a person sees it with all "
             f"{len(record['exceptions'])} exception(s) and {len(record['review_signals'])} signal(s).")
            + (f" {', '.join(whole)} {'applies' if len(whole) == 1 else 'apply'} to the whole receipt, so "
               f"{'it cites' if len(whole) == 1 else 'they cite'} no line and {'is' if len(whole) == 1 else 'are'} not in "
               "the table below." if whole else "")
            + f"\n- **Each line** goes to the review queue (the Review page) if the AI was unsure (EXC-03), a flag or review cites "
              f"it, it is in the lookup's 2% audit, or the lookup and the AI disagree. Here {len(queued)} of {len(routed)} lines do. "
              "People's decisions there feed the lookup's memory through `make learn`.")
        st.dataframe(pd.DataFrame([{
            "line": q["line_id"],
            "text": q["text"],
            "goes to": "review queue" if q["reasons"] else "no review needed",
            "why": "; ".join(q["reasons"]) or "the AI was confident and no flag or review cites this line",
        } for q in routed]), hide_index=True, width="stretch")

# --- the final output, the four things the brief asks for, as recorded in outputs/results.jsonl --------
with st.expander("Final output for this receipt", icon=":material/assignment_turned_in:"):
    st.caption(f"Exactly what the system recorded for {rid} in `{data.SOURCES[source].relative_to(data.ROOT)}`: the "
               "disposition, a category for every line, the exceptions and the review signals. Laid out as tables "
               "rather than JSON.")
    st.markdown(f"**Disposition:** {record['disposition']}")
    st.markdown("**Categories**")
    st.dataframe(pd.DataFrame([{
        "line": line["line_id"],
        "text": line["description_raw"],
        "category": line["category"],
        "restricted": line["restricted"],
        "confidence": line["confidence"],
    } for line in record["line_items"]]).style.format({"confidence": "{:.3f}"}), hide_index=True, width="stretch")
    st.markdown("**Exceptions**")
    if record["exceptions"]:
        st.dataframe(pd.DataFrame([{
            "rule": e["rule_id"],
            "severity": e["severity"],
            "lines": ", ".join(e["line_ids"]) or "whole receipt",
            "policy document": e["policy_doc"],
            "explanation": e["explanation"],
        } for e in record["exceptions"]]), hide_index=True, width="stretch")
    else:
        st.write("None.")
    st.markdown("**Review signals**")
    if record["review_signals"]:
        st.dataframe(pd.DataFrame([{
            "rule": s["rule_id"],
            "lines": ", ".join(s.get("line_ids", [])) or "whole receipt",
            "policy document": s["policy_doc"],
            "reason": s["reason"],
        } for s in record["review_signals"]]), hide_index=True, width="stretch")
    else:
        st.write("None.")
