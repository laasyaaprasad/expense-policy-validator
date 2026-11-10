"""How to explain a receipt: what makes it worth a look, and the five steps to walk through, built from the system's
committed outputs (results, trace, retrieval and the keyword baseline). Nothing here is written by hand per receipt."""

from viewer import data

LANGUAGES = {"es": "Spanish", "fr": "French", "de": "German", "ja": "Japanese", "pt": "Portuguese", "it": "Italian"}


def md(text: str) -> str:
    return str(text).replace("$", r"\$")


def short(line_id: str) -> str:
    return line_id.rsplit("-", 1)[-1]


def why_it_matters(rid: str) -> list[str]:
    """The facts that make this receipt interesting, only those that are true for it."""
    record, answer = data.results(data.DEFAULT_SOURCE)[rid], data.gold()[rid]
    base = data.results("Keyword baseline")[rid]
    receipt, trace, rules = data.receipts()[rid], data.traces()[rid], data.rules()
    engine, texts = trace["engine"], {line.line_id: line.description_raw for line in receipt.line_items}
    ours = {line["line_id"]: line for line in record["line_items"]}
    points = []

    if base["disposition"] != answer.disposition:
        fired = [e["rule_id"] for e in base["exceptions"]] + [s["rule_id"] for s in base["review_signals"]]
        points.append(f"**The keyword baseline gets it wrong:** it says {base['disposition']}"
                      + (f" ({', '.join(fired)})" if fired else "") + f", the correct answer is {answer.disposition}.")
    misread = [(line["line_id"], line["category"]) for line in base["line_items"]
               if line["category"] != ours[line["line_id"]]["category"]]
    if misread:
        points.append("**Keywords read it differently:** " + "; ".join(
            f"{short(i)} “{md(texts[i])}” is " + ("not recognised by keywords (other)" if cat == "other" else f"{cat} to keywords")
            + f", {ours[i]['category']} to the model" for i, cat in misread) + ".")
    foreign = [line for line in receipt.line_items if line.language != "en"]
    if foreign:
        glosses = data.glosses()
        points.append("**Not in English:** " + "; ".join(
            f"{short(line.line_id)} is {LANGUAGES.get(line.language, line.language)}, “{md(line.description_raw)}” → "
            f"“{md(glosses.get(line.line_id, ''))}”" for line in foreign) + ".")
    retrieved = data.retrieval_reasons().get(rid, {})
    overridden = [rule_id for rule_id in engine["overridden_by_region"] if rule_id in retrieved]  # ones that would have run
    if overridden:
        region = rules.region_of(receipt.employee_country)
        points.append(f"**A regional rule wins:** the employee is in {receipt.employee_country}, so {region}; its rules replace "
                      f"{', '.join(overridden)}.")
    for rule_id, lines in engine["lines_taken_over"].items():
        by = next((e["rule_id"] for e in record["exceptions"] if set(lines) & set(e["line_ids"])), "another rule")
        points.append(f"**One rule takes over from another:** {by} takes {', '.join(map(short, lines))} from {rule_id}, "
                      "so they aren't judged twice.")
    for rule_id, step in engine["rules"].items():
        if step["result"] == "replaced":
            points.append(f"**Across receipts:** {step['by']} replaces {rule_id}, so the split is reported, not the cap.")
    meal_day = engine["totals"].get("meal_day", {})
    if len(meal_day.get("receipts", [])) > 1:
        points.append(f"**Added up across receipts:** meal spend on {meal_day['date']} combines "
                      f"{', '.join(meal_day['receipts'])}.")
    named = [line for line in record["line_items"] if line.get("recipient_type")]
    if named:
        points.append("**A recipient is read from the text:** " + "; ".join(
            f"{short(line['line_id'])} is for {md(line['recipient_name'] or 'someone')} ({line['recipient_type']})" for line in named) + ".")
    unsure = [line for line in record["line_items"] if line["confidence"] < 0.9]
    if unsure:
        points.append("**The model was least sure here:** " + "; ".join(
            f"{short(line['line_id'])} {line['category']} at {line['confidence']:.3f}" for line in unsure) + ".")
    by_lookup = [x["line_id"] for x in trace["classify"]["lines"] if x.get("decided_by", "").startswith("lookup")]
    if by_lookup:
        points.append(f"**Decided by the lookup, not the model:** {', '.join(map(short, by_lookup))} matched a line a person reviewed.")
    if not receipt.itemized:
        points.append("**Not itemized:** only a total, so no line can be classified.")
    if rules.mcc_tier(receipt.mcc) == "high":
        points.append(f"**High-risk merchant:** MCC {receipt.mcc} raises MCC-01 on its own, whatever was bought.")
    severities = {e["severity"] for e in record["exceptions"]}
    if "critical" in severities:
        points.append("**Critical severity,** the most serious level a rule can raise.")
    if record["disposition"] == "review" and (severities <= {"low"}):
        points.append("**Review, not flag:** only low-severity exceptions or review signals.")
    if receipt.pre_approval_ref and record["exceptions"] and not any(
            rules.rule(e["rule_id"]).requires_reference for e in record["exceptions"]):
        points.append(f"**A reference that doesn't help:** {receipt.pre_approval_ref} is recorded, but no rule that fired "
                      "can be waived by it.")
    restricted = [line for line in record["line_items"] if line["restricted"]]
    if record["disposition"] == "pass" and restricted:
        points.append("**Restricted, but allowed:** " + ", ".join(
            f"{short(line['line_id'])} is {line['category']}" for line in restricted)
            + ", and the receipt meets every condition for it, so nothing fires.")
    if not points:
        if record["disposition"] == "pass":
            points.append("**A clean control:** nothing restricted and every amount within its limit. It shows the system "
                          "doesn't raise false alarms.")
        else:
            points.append("**A direct case:** " + "; ".join(
                f"{e['rule_id']} ({e['severity']}) on "
                + (", ".join(f"{short(i)} “{md(texts.get(i, ''))}”" for i in e["line_ids"]) or "the whole receipt")
                for e in record["exceptions"])
                + (". The rule applies" if len(record["exceptions"]) == 1 else ". Each rule applies")
                + " as written; nothing else changes the outcome.")
    return points


def walk_through(rid: str) -> list[str]:
    """The five steps to explain any receipt, filled in from its trace."""
    record, receipt, trace, rules = (data.results(data.DEFAULT_SOURCE)[rid], data.receipts()[rid], data.traces()[rid],
                                     data.rules())
    region = rules.region_of(receipt.employee_country)
    facts = [f"{md(receipt.merchant_name or 'no merchant')} (MCC {receipt.mcc}, {rules.mcc_tier(receipt.mcc)} risk)",
             receipt.submitted_expense_type, f"{receipt.employee_country}, so {region}", f"\\${receipt.total_amount_usd:,.2f}"]
    if receipt.attendee_count:
        facts.append(f"{receipt.attendee_count} attendees")
    facts.append(f"a {receipt.trip_nights}-night trip" if receipt.trip_id else "no trip")
    if receipt.pre_approval_ref:
        facts.append(f"reference {receipt.pre_approval_ref}")

    glosses = data.glosses()
    lines = "; ".join(
        f"{short(line['line_id'])} “{md(line['description_raw'])}”"
        + (f" (“{md(glosses[line['line_id']])}”)" if line["line_id"] in glosses else "")
        + f" → {line['category']}" for line in record["line_items"])

    reasons = data.retrieval_reasons().get(rid, {})
    specific = [f"{rule_id} ({why})" for rule_id, why in reasons.items() if why != "always"]
    always = sum(why == "always" for why in reasons.values())
    retrieved = (f"{len(reasons)} rules: " + (", ".join(specific) + f", plus {always} that run on every receipt"
                                               if specific else f"only the {always} that run on every receipt"))

    found = [f"{e['rule_id']} ({e['severity']}): {md(e['explanation']).rstrip('.')}" for e in record["exceptions"]]
    found += [f"{s['rule_id']} (signal): {md(s['reason']).rstrip('.')}" for s in record["review_signals"]]
    order = ["critical", "high", "medium", "low"]
    worst = min((e["severity"] for e in record["exceptions"]), key=order.index, default=None)
    because = (f"the most severe exception is {worst}" if worst and worst != "low"
               else "only low exceptions or signals were raised" if worst or record["review_signals"] else "nothing was raised")
    return [
        "**What it is:** " + ", ".join(facts) + ".",
        "**Reading the lines (model):** " + lines + ".",
        "**Rules retrieved (code):** " + retrieved + ".",
        "**What the engine found (code):** " + ("; ".join(found) if found else "every check passed") + ".",
        f"**Verdict:** {record['disposition']}, because {because}. Critical, high or medium means flag; low or a signal "
        "means review; nothing means pass.",
    ]
