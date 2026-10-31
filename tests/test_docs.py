"""The written numbers agree with the measured ones."""

import json

from validator.data import ROOT

USAGE = json.loads((ROOT / "outputs" / "metrics.json").read_text(encoding="utf-8"))["usage"]["per_receipt"]


def test_the_aws_cost_uses_the_measured_tokens_per_receipt():
    page = (ROOT / "docs" / "aws-design.md").read_text(encoding="utf-8")
    cached, total, output = (round(USAGE[k]) for k in ("prompt_cache_hit_tokens", "prompt_tokens", "completion_tokens"))
    assert f"{cached} cached" in page and f"{total - cached} uncached" in page and f"{output} output" in page
    assert f"${USAGE['usd']:.6f} per receipt" in page


def test_the_readme_spend_is_the_ledger_total():
    from validator.llm import ledger_total
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"**${ledger_total():.4f} over" in readme
