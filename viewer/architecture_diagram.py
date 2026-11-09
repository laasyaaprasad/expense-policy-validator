"""Architecture diagram, drawn in the page: the hand-placed SVG in docs/diagrams/architecture.html (the source that
docs/architecture-clean.png is rendered from), so its lines, corners and colours are exactly the diagram's."""

import re

import streamlit as st

from validator.data import ROOT

SOURCE = ROOT / "docs" / "diagrams"


def scoped_css(css: str, scope: str = ".arch") -> str:
    """The diagram's stylesheet, with every rule limited to the diagram, so it can't restyle the rest of the page."""
    rules = []
    for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        selectors = selectors.strip()
        if selectors == "html,body":
            continue  # the page's own background stays
        if selectors == "svg":  # the source draws at a fixed 680 px; here it fits the column instead
            body = body.replace("width:680px", "width:100%")
        if selectors == ":root":
            rules.append(f"{scope}{{{body}}}")
            continue
        rules.append(",".join(f"{scope} {s.strip()}" for s in selectors.split(",")) + f"{{{body}}}")
    return "\n".join(rules)


def diagram() -> str:
    page = (SOURCE / "architecture.html").read_text(encoding="utf-8")
    svg = page[page.index("<svg"):page.index("</svg>") + len("</svg>")]
    css = scoped_css((SOURCE / "style.css").read_text(encoding="utf-8"))
    # A white card, as in the PNG, so the diagram's light colours read the same on a dark page.
    html = (f"<style>{css}\n.arch{{background:#fff;border-radius:12px;padding:16px;max-width:720px;margin:0 auto}}"
            f"</style><div class=\"arch\">{svg}</div>")
    return html.replace("\n", " ")  # one line: Markdown would end the HTML block at the first line break


st.title("Architecture diagram")
st.markdown(diagram(), unsafe_allow_html=True)  # st.html would strip the SVG
