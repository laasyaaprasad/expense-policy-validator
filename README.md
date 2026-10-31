# Expense Policy Validator

A language model says what each receipt line is. Plain code then checks the receipt against the nine policies and quotes the policy for every exception. The results are in [`outputs/results.jsonl`](outputs/results.jsonl). See also the [evaluation](docs/evaluation.md), [AWS design](docs/aws-design.md), [research notes](DECISIONS.md) and [working log](NOTES.md).

**Setup.** Install Python 3.12 and [uv](https://docs.astral.sh/uv/), copy the organisers' `data/` folder to the repo root, and run `uv sync`. `make test` runs the offline tests. `make run`, `make eval`, `make baseline` and `make app` (the viewer) replay the committed model cache, so none of them needs a key. New model calls need a `.env`, set up as in `.env.example` for DeepSeek or OpenRouter.

## Architecture

Before any code, I asked how expense tools actually do this, because I wanted the boring version that is known to work. Expensify, Navan, Concur and Ramp's policy agent all draw the same line: AI reads the receipt, and limits, category lists and MCC rules stay plain configuration. So does this project.

The pipeline is normalise → translate → classify → retrieve → engine → report. It runs over all 32 receipts at once, because some caps add up spend across receipts. Non-English lines get an English gloss beside the original. One classifier call per receipt returns a category, item kind, recipient and reason for each line, and code validates the reply, with one retry. `restricted` comes from the taxonomy, not the model, and confidence is the probability of the category tokens. Retrieval keeps the rules whose `applies_to` matches, 7.1 of 31 per receipt, and missed none. Every call is cached, so a rerun is free and writes the same bytes.

## Prompts

The classification prompt ([classifier.py](validator/classifier.py)) defines the 21 categories in its own words, not the receipts' wording. It tells the model to classify what an item is rather than the words in its name, to keep category apart from purpose, and to treat the merchant as context only. `category` comes before `reason` in the reply, so the confidence measures the answer, not a justification.

The translation prompt asks for a short, literal gloss and tells the model not to classify. All prompt work was done on a hand-written dev set of 43 items in six languages, never on the receipts.

## Policy engine

One requirement I set up front decided the build order: if the finished system got a receipt wrong, I wanted to know which stage did it. So the deterministic half came first, tested on the answer key's categories ("oracle mode") before any model was connected. Later, a blame report swaps in the correct answer one stage at a time to name the stage at fault.

[`rules/rules.yaml`](rules/rules.yaml) holds 37 rules, each with a severity, parameters, `applies_to`, and an `evidence` quote that a test checks word for word against the policy. The 31 that can fire each have a small Python check. The engine builds the cross-receipt totals (meals per employee per day, OTC spend per trip), applies regional overrides, and runs the checks with `Decimal` money and inclusive caps. A rule that can't be applied is reported as `not_evaluated`, not passed. EXC-03, then EXC-01, sets the disposition.

**Confidence threshold: 0.5.** A line below it raises EXC-03 and is left out of every other rule. The rule for choosing the threshold was fixed before the dev set ran. Every dev item came back right at 0.96 or higher, so the default stood.

Oracle mode matched all 28 in-scope receipts on its first run, so no rule was changed to fit a label. It does mean the engine saw the answers before the freeze. Switching off any one of the 31 checks fails a test, and 30 synthetic receipts cover the 11 rules no gold receipt raises.

## Evaluation summary

When I asked what we would test against, the awkward answer was that the 32 labelled receipts are also the only test set. So the prompts were developed on the dev set, the config was tagged `frozen`, and its first real run was committed before it was scored. Nothing changed after that, so that run is the final one. The numbers cover the 28 in-scope receipts, with 95% Wilson intervals.

| | System | Keyword baseline |
|---|---|---|
| Macro F1 / restricted F1 (65 items) | 1.000 / 1.000 | 0.738 / 0.791 |
| Receipts exactly right | 28 / 28 (88–100%) | 20 / 28 |
| False positives · missed violations | 0 / 9 (0–30%) · 0 / 16 (0–19%) | 2 / 9 · 2 / 16 |

The clean score mostly says the traps are the taxonomy's own examples, and a fresh repeat flipped none of the 79 answers. It proves less than it looks. The prompt's authors had seen these receipts, and with nine clean receipts a 30% false-positive rate is still inside the interval. The keyword baseline falls for beer-battered cod and misses brand names, untranslated lines and the public official on RCP-022. We only get the out-of-scope timing receipts wrong (a lunch and dinner split around the meal cap, an overnight charge), because the TIME rules aren't built.

The set is also 57% violations, where a real stream has a few percent, so I wanted more data to check the system on. I ran it unchanged on 100 public receipts from Indonesian cafés and shops (CORD). It got 93% of lines right and caught both real violations, though only 2 of its 8 flags were right, mostly over bags and boxes the taxonomy has no category for. It also exposed a crash none of the 32 had hit, now fixed without changing any result on the 32. Nothing was tuned on it ([details](cord/README.md)).

## AI collaboration

Claude Code (Claude Opus 5.5) wrote most of the code, tests and docs. I set the working rules, made the calls and checked the results, but most of my steering was questions. The ones above (how real tools split the work, which stage gets the blame, what we test on, whether it holds up on real receipts) each changed what got built. NOTES.md logs those calls and every mistake the assistant made. For example, at temperature 0 DeepSeek gives every chosen token a logprob of 0.0, and the planned smoke test passed on those meaningless numbers until someone read the raw values. The classifier now runs at temperature 1, and a test checks that its logprobs carry information.

I signed off the 11 translation glosses by hand (`review/translations.csv`). The three recipients in `review/item_attributes.csv` were annotated from the line text.

Anthropic, through the assistant, saw the brief, policies, receipts and answer key. DeepSeek's API saw line text, merchant, MCC description and expense type, plus the public CORD lines, but never amounts, employee fields or labels. OpenRouter saw nothing.

## Spend

**$0.0759 over 350 calls** at peak prices (`outputs/spend.jsonl`). The final run cost $0.0074 and the CORD pilot $0.0394. The rest went on development and checks.

I had half expected to use Jev, the new classification model, because it returns probabilities for free. A head-to-head talked me out of it. Jev is weakest with many categories and with non-English text, and this data has both. In the one comparison I found, it trailed DeepSeek V4.1 Flash, 72.5% to 76%. So the model is `deepseek-flash` (DeepSeek V4.1 Flash) on DeepSeek's API. It gives the logprobs the confidence method needs, handles all six languages, and costs about $0.00025 a receipt. I only had a DeepSeek key, so the OpenRouter path is built but has never run.

## What I would do next

- Build the TIME rules.
- Calibrate the threshold and measure flag precision on a labelled sample of the company's own receipts.
- Rerun the gates through OpenRouter.
- Send names to the model only as placeholders.
