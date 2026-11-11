"""AWS deployment: the diagram and the cost calculation, read from docs/aws-design.md."""

import re

import streamlit as st

from validator.data import ROOT

DOC = (ROOT / "docs" / "aws-design.md").read_text(encoding="utf-8")


def section(start: str, end: str | None = None) -> str:
    """The doc from the line starting `start` up to the line starting `end`, with dollar signs escaped so a pair
    doesn't render as a formula."""
    body = DOC[DOC.index(start):DOC.index(end) if end else None]
    return body.replace("$", r"\$").strip()


st.title("AWS deployment and cost")
st.caption("A design on paper, as the brief asks: nothing is deployed. Everything here is read from "
           "`docs/aws-design.md`. Coral is the model (outside AWS), purple the lookup, teal people.")

_, picture, _ = st.columns([1, 3, 1])
picture.image(str(ROOT / "docs" / "aws-deployment-clean.png"), width="stretch")

st.subheader("Cost at 50,000 receipts a day")
total = re.search(r"\*\*About \$(\d+) a month\*\*, (\d+)% of it the model", DOC)
model = re.search(r"\| Model \(OpenRouter, Fireworks\) \|.*\| ≈ (\d+) \|", DOC)
tiles = st.columns(3)
tiles[0].metric("Per month", f"about ${total.group(1)}")
tiles[1].metric("The model's share", f"{total.group(2)}%", help=f"About ${model.group(1)} a month in model calls.")
tiles[2].metric("Per receipt", f"${int(total.group(1)) / 1.52e6:.5f}", help="Over 1.52M receipts a month (50,000 × 30.4).")
st.markdown(section("**Cost at", "**Before production.**"))

st.subheader("Before production")
st.markdown(section("**Before production.**").removeprefix(r"**Before production.**").strip())
