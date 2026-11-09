"""The first page: the problem, what makes it hard and the approach. The numbers are on the summary page, at the end."""

import streamlit as st

from viewer import data

HARD = [
    ("Names that mislead", "Beer-battered cod is food. A Moscow Mule mug is a mug."),
    ("Six languages", "“Limpeza a seco - 2 camisas” is laundry, not clothing."),
    ("Regional overrides", "APAC bans alcohol the global rules would allow."),
    ("Carve-outs", "A reference waives a rule only if it is the right kind."),
    ("Across receipts", "Two meals each under the cap, together over it."),
    ("False alarms", "Nine receipts look suspicious and should pass."),
]


st.title("Expense Policy Validator")
st.header("Decide flag, review or pass for each of 32 receipts, against nine policies, quoting the policy for every "
          "exception", divider="gray")
for row in (HARD[:3], HARD[3:]):
    for column, (name, example) in zip(st.columns(3), row):
        with column.container(border=True, height="stretch"):
            st.subheader(name)
            st.markdown(example)

TOOLS = [("Expensify", "expensify.com"), ("Navan", "navan.com"), ("SAP Concur", "concur.com"), ("Ramp", "ramp.com")]
# Each company's own site icon, loaded by the browser when the page shows (nothing is stored in the repo).
LOGO = "https://www.google.com/s2/favicons?domain={}&sz=128"

st.subheader("Approach", text_alignment="center")
with st.container(border=True):
    st.markdown(
        '<div style="display:flex;justify-content:space-around;flex-wrap:wrap;gap:16px;padding:8px 0">'
        + "".join(f'<div style="text-align:center"><img src="{LOGO.format(domain)}" alt="" '
                  f'style="width:56px;height:56px;border-radius:12px"><div style="margin-top:6px;font-weight:600">'
                  f"{name}</div></div>" for name, domain in TOOLS)
        + "</div>", unsafe_allow_html=True)
    st.markdown('<p style="text-align:center;font-size:1.2rem;margin:4px 0 8px"><b>All four work the same way: AI reads '
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
st.markdown("#### Three designs considered", text_alignment="center")
for column, (icon, name, verdict, badge_icon, color, why) in zip(st.columns(3), DESIGNS):
    with column.container(border=True, height="stretch"):
        st.markdown(f"**{icon} {name}**")
        st.badge(verdict, icon=badge_icon, color=color)
        st.caption(why)
