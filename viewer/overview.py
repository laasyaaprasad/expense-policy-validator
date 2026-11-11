"""The first page: the problem, what makes it hard and the approach. The numbers are on the summary page, at the end."""

import streamlit as st

from viewer import data

HARD = [
    (":material/theater_comedy:", "Names that mislead", "Beer-battered cod is food. A Moscow Mule mug is a mug."),
    (":material/translate:", "Six languages", "“Limpeza a seco - 2 camisas” is laundry, not clothing."),
    (":material/public:", "Regional overrides", "APAC bans alcohol the global rules would allow."),
    (":material/verified:", "Carve-outs", "A reference waives a rule only if it is the right kind."),
    (":material/call_split:", "Across receipts", "Two meals each under the cap, together over it."),
    (":material/notifications_off:", "False alarms", "Nine receipts look suspicious and should pass."),
]

# Page-only styling: a lead line under the title, softer cards, and a highlight on the selected design.
st.markdown("""<style>
[data-testid="stMarkdownContainer"] p.lead{font-size:1.35rem;line-height:1.45;opacity:.85;margin:-.4rem 0 1rem;max-width:62rem}
[data-testid="stMarkdownContainer"] p.eyebrow{font-size:.8rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;opacity:.6;margin:1.2rem 0 0}
[data-testid="stMarkdownContainer"] p.card-title{font-size:1.15rem;font-weight:700;margin:0 0 .25rem}
[data-testid="stMarkdownContainer"] p.card-body{font-size:1rem;opacity:.8;margin:0}
.st-key-selected_design{border:2px solid #21c354 !important;background:rgba(33,195,84,.06)}
</style>""", unsafe_allow_html=True)

st.title("Expense Policy Validator")
st.markdown('<p class="lead">Decide <b>flag</b>, <b>review</b> or <b>pass</b> for each of 32 receipts, against nine '
            "policies, quoting the policy for every exception.</p>", unsafe_allow_html=True)

st.markdown('<p class="eyebrow">What makes it hard</p>', unsafe_allow_html=True)
for row in (HARD[:3], HARD[3:]):
    for column, (icon, name, example) in zip(st.columns(3), row):
        with column.container(border=True, height="stretch"):
            st.markdown(f"### {icon}")
            st.markdown(f'<p class="card-title">{name}</p><p class="card-body">{example}</p>', unsafe_allow_html=True)

TOOLS = [("Expensify", "expensify.com"), ("Navan", "navan.com"), ("SAP Concur", "concur.com"), ("Ramp", "ramp.com")]
# Each company's own site icon, loaded by the browser when the page shows (nothing is stored in the repo).
LOGO = "https://www.google.com/s2/favicons?domain={}&sz=128"

st.markdown('<p class="eyebrow">Approach</p>', unsafe_allow_html=True)
with st.container(border=True):
    st.markdown(
        '<div style="display:flex;justify-content:space-around;flex-wrap:wrap;gap:16px;padding:8px 0">'
        + "".join(f'<div style="text-align:center"><img src="{LOGO.format(domain)}" alt="" '
                  f'style="width:52px;height:52px;border-radius:12px"><div style="margin-top:6px;font-weight:600">'
                  f"{name}</div></div>" for name, domain in TOOLS)
        + "</div>", unsafe_allow_html=True)
    st.markdown('<p style="text-align:center;font-size:1.15rem;margin:4px 0 8px"><b>All four work the same way: AI reads '
                'the receipt, and the rules are applied as configuration.</b></p>', unsafe_allow_html=True)

m = data.metrics()
ours, keywords = m["disposition"]["accuracy"], m["baseline"]["exact_match"]
DESIGNS = [
    (":material/smart_toy:", "An LLM judges each receipt", "Rejected", ":material/close:", "red",
     "Can't unit-test a cap the model applies, and the brief bars it from the verdict."),
    (":material/manage_search:", "Keywords and rules, no AI", f"Baseline · {keywords['k']} / {keywords['n']}",
     ":material/straighten:", "gray", "Free and repeatable, but misleading names beat it."),
    (":material/handshake:", "The model reads, code judges", f"Selected · {ours['k']} / {ours['n']}",
     ":material/check:", "green", "The industry pattern: testable and traceable."),
]
st.markdown('<p class="eyebrow">Three designs considered</p>', unsafe_allow_html=True)
for column, (icon, name, verdict, badge_icon, color, why) in zip(st.columns(3), DESIGNS):
    key = "selected_design" if color == "green" else None
    with column.container(border=True, height="stretch", key=key):
        st.markdown(f"**{icon} {name}**")
        st.badge(verdict, icon=badge_icon, color=color)
        st.caption(why)
