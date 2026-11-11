# Expense Policy Validator

A language model says what each receipt line is; plain code checks it against the nine policies and quotes the policy for every exception. Results: [`outputs/results.jsonl`](outputs/results.jsonl). More: [evaluation](docs/evaluation.md), [AWS design](docs/aws-design.md), [decisions](DECISIONS.md), [working log](NOTES.md).

**Setup:** Python 3.12 and [uv](https://docs.astral.sh/uv/); copy the organisers' `data/` into the repo, then `uv sync`. `make test`, `make run`, `make eval`, `make baseline` and `make app` (the viewer) run offline from the committed model cache. New model calls need `OPENROUTER_API_KEY` in `.env` (see `.env.example`).

## Research first

Before coding I studied how expense tools split the work: [Expensify](https://help.expensify.com/articles/new-expensify/workspaces/Workspace-Rules), [Navan](https://navan.com/blog/audit-engine) and [SAP Concur](https://www.sap.com/products/spend-management/concur-expense-configuration-for-audit-rules.html) keep limits and category rules as configuration, and even [Ramp's](https://support.ramp.com/policy-agent-overview) LLM-led agent layers fixed rules on top. Of three designs, an LLM judging each receipt was rejected (untestable, and the brief bars it from deciding EXC-01); keywords alone became the baseline (20 / 28); **the model reads, code judges** was picked (28 / 28). Other choices: [DECISIONS.md](DECISIONS.md).

## Architecture

<p align="center"><img src="docs/architecture-clean.png" alt="Architecture: the rulebook is built offline once; each receipt is normalised, looked up, translated and classified if new, then retrieved, checked by the engine and reported; unsure or flagged lines go to a review queue, and make learn feeds reviewed lines back to the lookup." width="680"></p>

Coral: the model. Purple: the lookup, with its lookup table in amber. Teal: people. Dotted: the rulebook feeds retrieval and the engine. The same diagram is drawn in the viewer's Architecture page, from [its source](docs/diagrams/architecture.html).

Each step is its own module in [`validator/`](validator); only translation and classification call the model, and every call is cached. The semantic lookup ([`lookup.py`](validator/lookup.py)) decides a line only when every word matches, by meaning, a line a person reviewed and nothing restricted is close; it never decides a restricted category, and let no blind look-alike trap through. On these receipts it is a cold start and decides 1 line. People's reviews fill its one shared memory through `make learn`, only when the model agrees (two keys). Design and tests: experiments 6–12 ([DECISIONS.md, section 12](DECISIONS.md)).

**End to end, RCP-011:** Cellar Door Fine Wines (MCC 5921), Client Entertainment, pre-approval PA-2026-0431. "Vino tinto Rioja Reserva 750ml" is glossed "Rioja Reserva red wine 750ml" and classified `alcohol` at 0.99996. Of 12 retrieved rules, the engine raises ALC-03 on L1 (alcohol bought where it isn't served) and MCC-01 (high-risk merchant), which the pre-approval doesn't waive: **flag**, as in the answer key. Every step is in [`outputs/traces.jsonl`](outputs/traces.jsonl).

## Prompts

The classification prompt ([classifier.py](validator/classifier.py)) defines the 21 categories in its own words and says to classify what an item is, not the words in its name, with the merchant as context only. `category` comes before `reason`, so the confidence (the category tokens' probability) measures the answer, not a justification. The translation prompt asks for a literal gloss. Both were tuned only on a 43-item hand-written dev set, by an assistant that had the answer key.

## Policy engine

- **Rules as data, logic as code:** [`rules/rules.yaml`](rules/rules.yaml) holds 40 rules (severity, numbers, `applies_to`, the policy quote word for word, checked by a test); the 34 that can fire each have a small Python check. A limit changes in config; the logic stays unit-tested.
- **A fixed order, no rule triggering another:** regional overrides, takeovers (TIME-03 over a meal cap, GIFT-04 over GIFT-01), each check, EXC-03, then EXC-01 for the disposition, so every outcome is traceable.
- **Carve-outs need the right recorded reference; `Decimal` money; inclusive caps.**
- **Tested:** on the answer key's categories the engine matches all 28 receipts, and switching off any check fails a test ([`test_check_coverage.py`](tests/test_check_coverage.py)). [`tests/test_engine.py`](tests/test_engine.py) covers the brief's five cases: RCP-027, RCP-016, RCP-006/026, RCP-031 and RCP-028.
- **Confidence threshold: 0.5,** fixed before any output was seen and held on the dev set; a line below it raises EXC-03 and is left out of every other rule. Kept low because every EXC-03 is scored as wrong.

## Evaluation summary

The frozen config's first run was committed before scoring; the later switch to temperature 0 ([experiment 6](experiments/semantic_first/README.md)) changed no result. The 28 in-scope receipts, with 95% Wilson intervals:

| | System | Keyword baseline |
|---|---|---|
| Item macro F1 / restricted F1 | 1.000 / 1.000 | 0.738 / 0.791 |
| Receipts exactly right | 28 / 28 (88–100%) | 20 / 28 |
| False positives · missed violations | 0 / 9 (0–30%) · 0 / 16 (0–19%) | 2 / 9 · 2 / 16 |

This proves less than it looks: the traps are the taxonomy's own examples, the prompt's author had seen these receipts, and nine clean receipts can't rule out a 30% false-positive rate. Reworded, it still got 64 / 65 items. On 100 real Indonesian receipts ([CORD](cord/README.md), assistant-labelled, 57 hand-checked) it got 94% of lines right but flagged or reviewed 10 of 98 clean ones.

## AI collaboration

Claude Code (Claude Opus 5.5) wrote most of the code, tests and docs; I made the calls and checked the results, and [NOTES.md](NOTES.md) logs where I directed, overrode or corrected it. **What it got wrong:** on the first provider, at temperature 0 every chosen token's logprob was 0.0, so the smoke test passed on meaningless numbers until the assistant read the raw values. **Checked by hand:** the 11 translations and 57 CORD labels. **Who saw the data:** Anthropic, through the assistant, saw the brief, policies, receipts and answer key. The model services saw only line text, merchant, MCC description and expense type: no amounts, employee fields or labels. They were OpenRouter (served by Fireworks) for the submitted run and the experiments, and, for experiment 2’s verifier on restricted lines only, Vercel AI Gateway and Baseten (Mammoth-1, used in place of Jev). The public CORD lines went to the same services.

## Spend

**About $0.45 over 2,949 calls on the OpenRouter key**, as billed (which the dashboard should show): $0.0575 for the submitted run's 378 calls (`outputs/spend.jsonl`, including the dev set, CORD and smoke tests) and $0.39 for the experiments (each in its own `spend.jsonl`). A full run costs $0.0058. The first run's 350 calls ($0.0759) went through DeepSeek's own API before the OpenRouter key arrived, so they aren't on the dashboard. Experiment 2's verifier made 54 more calls through Vercel AI Gateway and Baseten ($0.0011). **In all, the project spent $0.53 over 3,353 model calls**, not counting the coding assistant. **Model:** DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash`), which I pushed for: with reasoning off it led the open-weights models on Artificial Analysis's index ([archived](https://web.archive.org/web/20260925000203/https://artificialanalysis.ai/models/deepseek-v4-1-flash-non-reasoning)), uses 152 output tokens a receipt and returns the logprobs the confidence needs; reasoning would cost 3.4 times the tokens for no gain here ([why](DECISIONS.md#8-model)). Calls are pinned to Fireworks, then Together, the hosts that returned logprobs.

## What I would do next

Measure flag precision and calibrate both thresholds on the company's own receipts; say whether packaging sold with food is `other` (most CORD errors are bags and cups); send names to the model only as placeholders; gloss dish names as the dish ("TALAM UNGU" became "PURPLE TRAY").
