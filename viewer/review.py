"""Review: a person checks the lines worth a look and records the right category (validator/reviews.py).

Saving a review only appends it to the client's reviews.jsonl; nothing in the results changes. `make learn` applies the
reviews to the lookup's memory under the two-key rule, and `make run` then uses them.
"""

import json

import pandas as pd
import streamlit as st

from evaluation import cord
from validator.data import ROOT, load_receipts
from validator.lookup import DEFAULT_CLIENT, client_dir
from validator.reviews import TAXONOMY, add_review, load_reviews, review_queue, reviews_path

SETS = {
    "32 receipts": (lambda: load_receipts(), ROOT / "outputs" / "traces.jsonl", ROOT / "outputs" / "results.jsonl"),
    "CORD pilot": (lambda: load_receipts(cord.RECEIPTS_PATH), ROOT / "outputs" / "cord" / "traces.jsonl", ROOT / "outputs" / "cord" / "results.jsonl"),
}


def read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


st.title("Review")
st.caption("The lines worth a person's look: the AI was unsure (EXC-03), a flag or review cites them, they are in the lookup's audit "
           "sample, or the lookup and the AI disagree. Saving a review changes no result. Run `make learn` to apply the saved "
           "reviews to the lookup's memory (two keys: a safe line is remembered only if the AI agreed at 0.9 or more; a "
           "restricted one becomes a restricted reference), then `make run`.")

choice = st.radio("Receipts", list(SETS), horizontal=True)
every = st.toggle("Show every line, not only the queue")
load, traces_path, results_path = SETS[choice]
texts = {line.line_id: line.description_raw for r in load() for line in r.line_items}
queue = review_queue(read_jsonl(traces_path), read_jsonl(results_path), texts, every_line=every)

path = reviews_path(DEFAULT_CLIENT)
saved = load_reviews(path)
outcomes = {r["review_id"]: r["outcome"] for r in load_reviews(client_dir(DEFAULT_CLIENT) / "learn_log.jsonl")}
latest = {r["line_id"]: r for r in saved}

if not queue:
    st.info("Nothing is queued for review in this set.")
else:
    st.dataframe(pd.DataFrame([{
        "line": q["line_id"], "text": q["text"], "queued because": "; ".join(q["reasons"]) or "(every line shown)",
        "category now": q["category"], "confidence": q["confidence"], "decided by": q["decided_by"],
        "AI said": f"{q['model_category']} {q['model_confidence']:.3f}" if q["model_category"] else "",
        "reviewed": latest[q["line_id"]]["category"] if q["line_id"] in latest else "",
    } for q in queue]), hide_index=True, width="stretch")

    by_id = {q["line_id"]: q for q in queue}
    line_id = st.selectbox("Line to review", list(by_id), format_func=lambda k: f"{k} · {by_id[k]['text']}")
    item = by_id[line_id]
    categories = list(TAXONOMY)
    with st.form("review", clear_on_submit=False):
        category = st.selectbox("Correct category", categories, index=categories.index(item["category"]) if item["category"] in categories else 0,
                                format_func=lambda c: f"{c} (restricted)" if TAXONOMY[c] else c)
        sure = st.checkbox("I'm sure", value=True, help="Unsure reviews are kept but never learned.")
        reviewer = st.text_input("Your name", help="Every review records who made it.")
        submitted = st.form_submit_button("Save review")
    if submitted:
        if not reviewer.strip():
            st.error("Enter your name first: every review records who made it.")
        else:
            record = add_review(path, item, category, sure, reviewer)
            st.success(f"Saved: {record['line_id']} is {record['category']}" + ("" if sure else " (unsure)")
                       + ". It changes nothing until `make learn`.")
            saved = load_reviews(path)

st.subheader("Saved reviews")
if not saved:
    st.caption(f"None yet. Reviews are saved to {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}.")
else:
    st.dataframe(pd.DataFrame([{
        "line": r["line_id"], "text": r["text"], "category": r["category"], "sure": "yes" if r["sure"] else "no",
        "reviewer": r["reviewer"], "at": r["reviewed_at"], "AI said": r.get("model_category") or "",
        "learned": outcomes.get(r["review_id"], "waiting for make learn"),
    } for r in saved]), hide_index=True, width="stretch")
