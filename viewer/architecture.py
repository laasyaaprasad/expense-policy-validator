"""Architecture: the pipeline diagram."""

import streamlit as st

from validator.data import ROOT

st.title("Architecture")
st.caption("The same diagram is in the README (as Mermaid) and in `docs/architecture-clean.png`. Coral steps call the "
           "model, purple is the lookup, teal is people; everything else is plain code.")

_, picture, _ = st.columns([1, 3, 1])
picture.image(str(ROOT / "docs" / "architecture-clean.png"), width="stretch")
