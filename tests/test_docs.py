"""The written numbers agree with the measured ones."""

import json

from validator.data import ROOT

USAGE = json.loads((ROOT / "outputs" / "metrics.json").read_text(encoding="utf-8"))["usage"]["per_receipt"]


def test_the_aws_cost_uses_the_measured_tokens_per_receipt():
    page = (ROOT / "docs" / "aws-design.md").read_text(encoding="utf-8")
    cached, total, output = (round(USAGE[k]) for k in ("prompt_cache_hit_tokens", "prompt_tokens", "completion_tokens"))
    assert f"{cached} cached" in page and f"{total - cached} uncached" in page and f"{output} output" in page
    assert f"${USAGE['usd']:.6f} per receipt" in page


def openrouter_spend() -> tuple[int, float]:
    """Every call billed through the OpenRouter key: the submitted run's ledger and each experiment's (DeepInfra
    serves OpenRouter's embeddings). What the OpenRouter dashboard should show."""
    calls, usd = 0, 0.0
    for path in [ROOT / "outputs" / "spend.jsonl", *sorted((ROOT / "experiments").glob("*/spend.jsonl"))]:
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
            if row.get("provider") in ("openrouter", "DeepInfra") or "OpenRouter" in (row.get("endpoint") or ""):
                calls, usd = calls + 1, usd + (row.get("usd") or 0)
    return calls, usd


def test_the_readme_spend_matches_the_ledgers():
    from validator.llm import ledger_total
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    calls, usd = openrouter_spend()
    assert f"**About ${usd:.2f} over {calls:,} calls on the OpenRouter key**" in readme  # what the dashboard should show
    assert f"${ledger_total():.4f} for the submitted run" in readme


def test_the_readme_total_spend_matches_the_summary_page():
    from viewer.data import spend
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    totals = spend()
    calls, usd = totals["total"]
    assert f"**In all, the project spent ${usd:.2f} over {calls:,} model calls**" in readme
    other_calls, other_usd = totals["other gateways"]
    assert f"made {other_calls} more calls through Vercel AI Gateway and Baseten (${other_usd:.4f})" in readme
    first_calls, first_usd = totals["DeepSeek's own API"]
    assert f"The first run's {first_calls} calls (${first_usd:.4f})" in readme
