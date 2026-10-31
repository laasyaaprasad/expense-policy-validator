# Expense Policy Validator

An LLM classifies receipt lines; a deterministic engine checks each receipt against the nine policies, citing policy text for every exception. Results: [`outputs/v1/results.jsonl`](outputs/v1/results.jsonl). See also the [evaluation](docs/evaluation.md), the [AWS design](docs/aws-design.md), the [research notes](DECISIONS.md) and the [working log](NOTES.md).

**Setup.** Install Python 3.12 and [uv](https://docs.astral.sh/uv/), put the organisers' `data/` at the repo root, and run `uv sync`. Then run `make test` (offline), `make run`, `make eval`, `make baseline` and `make app` (the viewer). All of these replay the committed model cache, so no key is needed. New model calls need a `.env` (see `.env.example`) with `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY`, or `openrouter` and `OPENROUTER_API_KEY`.

## Architecture

The pipeline is normalise → **translate** → **classify** → retrieve → **engine** → report. It runs over all 32 receipts, because some caps add up spend across receipts.
- **Translate** glosses non-English lines in one temperature-0 call per receipt, and keeps the original text.
- **Classify** makes one call per receipt. It returns each line's category, item kind, recipient and reason. Code validates the reply and retries once. `restricted` comes from the taxonomy, and confidence is the probability of the category tokens, taken from logprobs.
- **Retrieve** picks the rules whose `applies_to` matches: 7.1 of 31 per receipt, with 100% recall.

Every call is cached and logged, so a rerun is byte-identical and makes no calls.

## Prompts

`classify-v1` ([classifier.py](validator/classifier.py)) defines the categories in wording written for this project, not copied from the receipts. It tells the model to classify what an item *is* rather than the words in its name, to keep category separate from purpose, and to treat the merchant as context only. `category` comes before `reason`, so the confidence measures the answer, not a rationale. The translation prompt (`translate-v1`) asks for a literal, brief gloss and forbids classifying, so the classifier sees the original and an unbiased reading. All prompt work used a hand-written dev set (43 items in six languages).

## Policy engine

[`rules/rules.yaml`](rules/rules.yaml) holds 37 rules, each with a severity, parameters, `applies_to` and an `evidence` quote. Tests check that each quote is verbatim policy text. Each of the 31 rules that can fire has its own Python check. The engine first builds the cross-receipt totals: meals per employee-day and OTC spend per trip. Then, per receipt, it applies regional overrides and runs the checks, using `Decimal` money and inclusive caps. It honours waivers by reference kind (`PA-` waives only TECH-02), reports any rule it can't apply as `not_evaluated`, and sets the disposition with EXC-03 and then EXC-01.

**Confidence threshold: 0.5.** A line below it raises EXC-03 and is left out of every rule. The rule for choosing the threshold was fixed before the dev set ran. Every dev item came back right with confidence above 0.96, so the default stood.

**Oracle mode.** The engine was built against the answer key (gold categories and hand-annotated recipients) before the freeze. So v1 is blind for the classifier and the threshold, but not for the engine. Oracle mode matched 28/28 on its first run, so no rule was bent to fit a label. Two further guards: disabling any one of the 31 checks breaks a test, and 30 synthetic receipts cover the 11 rules no gold receipt raises.

## Evaluation summary

The config was tagged `v1-frozen`, and its first real run was committed before it was scored. Nothing changed after that, so **v1 (blind) is final**. Results cover the 28 in-scope receipts; intervals are 95% Wilson.

| | System v1 = final | Keyword baseline |
|---|---|---|
| Macro F1 / restricted F1 (65 items) | 1.000 / 1.000 | 0.738 / 0.791 |
| Receipts exactly right | 28 / 28 (88–100%) | 20 / 28 |
| False positives · missed violations | 0 / 9 (0–30%) · 0 / 16 (0–19%) | 2 / 9 · 2 / 16 |

- **Why so clean.** The traps are the taxonomy's own examples, and a fresh repeat flipped none of the 79 answers. But the prompt's authors had seen the receipts, and with only nine clean receipts a 30% false-positive rate is still possible.
- **The baseline** falls for beer-battered cod and misses brand names, untranslated lines, and the public official on RCP-022.
- **Failures.** None in scope. Both failure cases come from the out-of-scope timing receipts: RCP-020/021 split a lunch and a dinner around the meal cap (TIME-03), and RCP-029 is an overnight charge (TIME-01). Both are rule logic, because the TIME rules aren't built.
- **Base rate.** This set is 57% violations, while a real stream has a few percent, so false flags would dominate.
- **Stress test on real receipts (CORD).** To see how v1 behaves outside the curated set, it was run unchanged on 100 public Indonesian receipts, hand-labelled, 2% of them violations. This was a check, not a target, and nothing was tuned on it.
  - v1 got 93% of lines right and found both violations.
  - Only 2 of its 8 flags were right, mostly because packaging was read as household goods, which the taxonomy doesn't settle.
  - It exposed a crash, now fixed; the fix changes no v1 output ([evaluation, section 8](docs/evaluation.md)).

## Walkthrough: RCP-011

Cellar Door Fine Wines (MCC 5921), declared as "Client Entertainment", with pre-approval PA-2026-0431. The line "Vino tinto Rioja Reserva 750ml" is glossed "Rioja Reserva red wine" and classified `alcohol`, restricted, at confidence 0.99999. Retrieval picks 10 rules. The engine raises ALC-03 (high, L1: alcohol bought where it isn't served) and MCC-01 (high: a high-risk MCC). The pre-approval waives neither, so the result is **flag**, matching the answer key line for line. Each exception carries its verbatim policy text and an explanation built from the computed facts.

## AI collaboration

Claude Code (Claude Opus 5.5) researched, planned and drafted the code, tests and docs, one gated milestone at a time. NOTES.md logs every mistake it made. Two examples:
- At temperature 0, DeepSeek gives every chosen token logprob 0.0, so the planned smoke test passed on meaningless confidences. Reading the raw values caught it; the classifier now runs at temperature 1, and a test asserts the logprobs are informative.
- The viewer lost the results-source choice on `st.switch_page`. Clicking through the app caught it; no test could.

**Checked by hand:** the 11 translation glosses and the 3 recipient annotations (`review/`).

**Where the data went:** Anthropic (through the assistant) saw the brief, policies, receipts and answer key. DeepSeek's API saw line text, merchant, MCC description and expense type, never amounts, employee fields or labels. OpenRouter saw nothing.

## Spend

**$0.0759 over 350 calls** at peak prices (`outputs/spend.jsonl`), of which the v1 run was $0.0074 and the CORD pilot $0.0394. The model is `deepseek-flash` (DeepSeek V4.1 Flash) on DeepSeek's API. It returns the logprobs the confidence method needs, handles all six languages and costs about $0.00025 per receipt. Only a DeepSeek key was available, so the OpenRouter path is built but has not been run.

## What I would do next

Build the TIME rules. Calibrate the confidence threshold and measure flag precision on a labelled sample of the company's own receipts. CORD showed confident errors (0.96–1.0) that 0.5 lets through. Ask whether a free ($0) line should raise an item exception, as it does now. Rerun the gates through OpenRouter. Send names to the model only as placeholders.
