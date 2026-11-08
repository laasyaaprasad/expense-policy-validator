# Expense Policy Validator

A language model says what each receipt line is; plain code checks it against the nine policies and quotes the policy for every exception. Results: [`outputs/results.jsonl`](outputs/results.jsonl). More: [evaluation](docs/evaluation.md), [AWS design](docs/aws-design.md), [decisions](DECISIONS.md), [working log](NOTES.md). **Setup:** with Python 3.12 and [uv](https://docs.astral.sh/uv/), copy the organisers' `data/` into the repo and run `uv sync`. `make test`, `make run`, `make eval`, `make baseline` and `make app` (the viewer) run offline from the committed model cache. To teach the lookup: review lines on the viewer's Review page, then `make learn-dry` and `make learn` (needs `uv sync --group semantic` for new text), then `make run`; `make reset-reviews` undoes it. New model calls need `OPENROUTER_API_KEY` in a `.env` (see `.env.example`).

## Research first

Before writing any code, I spent 45 minutes studying how real expense tools divide the work. [Expensify](https://help.expensify.com/articles/new-expensify/workspaces/Workspace-Rules), [Navan](https://navan.com/blog/audit-engine) and [SAP Concur](https://www.sap.com/products/spend-management/concur-expense-configuration-for-audit-rules.html) apply limits, category lists and merchant-code rules as configuration; even [Ramp's](https://support.ramp.com/policy-agent-overview) LLM-led Policy Agent layers fixed rules on top. The pattern: AI reads the receipt, rules decide. Of three designs:
- **An LLM judges each receipt:** rejected; caps and precedence done by a model can't be tested or repeated, and the brief bars it from deciding EXC-01.
- **Keywords and rules, no model:** free and repeatable, but misleading names beat it; kept as the baseline (20 / 28).
- **The model reads, code judges:** picked; the industry pattern, testable and traceable (28 / 28). Later choices and their alternatives: [DECISIONS.md](DECISIONS.md).

## Architecture

```mermaid
flowchart TB
    R["receipt<br/>labels dropped"] --> N["normalise<br/>clean-up and code checks · normaliser.py"]
    N --> L{"seen it before?<br/>semantic lookup · lookup.py"}
    L -- "no" --> T["translate<br/>non-English lines only · translate.py"]
    T --> K["classify<br/>AI classifier, temp 0 · classifier.py"]
    K --> P["retrieve<br/>rules whose applies_to match · retriever.py"]
    L -- "yes: reuse the answer, skip the AI" --> P
    P --> E["engine<br/>one Python check per rule, no AI · engine/"]
    E --> O["report<br/>pass, flag or review, with policy quotes · reporter.py"]
    O -- "confident (0.5 or above)<br/>and no flag or review" --> A["done automatically<br/>no person needed"]
    O -- "any of: AI unsure (below 0.5, EXC-03)<br/>· cited by a flag or review<br/>· in the lookup's 2% audit<br/>· lookup and AI disagree" --> Q["review queue<br/>a person decides · viewer/review.py"]
    Q --> G["make learn<br/>two keys, one table per client · learn.py"]
    G -- "↻ reviewed lines feed the lookup" --> L
    subgraph offline["offline, once: the rulebook"]
        direction LR
        D["policy docs<br/>nine policies"] --> Y1["LLM drafts<br/>first pass"] --> Y2["person checks<br/>quotes tested"] --> Y[("rules.yaml<br/>40 rules")]
    end
    Y -.-> P
    Y -.-> E
    classDef default fill:#F7F7F5,stroke:#B4B2A9,color:#2C2C2A
    classDef ai fill:#FAECE7,stroke:#993C1D,color:#712B13
    classDef lookup fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    classDef people fill:#E1F5EE,stroke:#0F6E56,color:#085041
    class Y1,T,K ai
    class L lookup
    class Y2,Q,G people
    style offline fill:#FFFFFF,stroke:#B4B2A9,stroke-dasharray:4 4,color:#2C2C2A
    linkStyle 5 stroke:#7F77DD
    linkStyle 11 stroke:#1D9E75
```

Coral: the model. Purple: the lookup. Teal: people. Dotted: the rulebook feeds retrieval and the engine. As an image: [docs/architecture-clean.png](docs/architecture-clean.png) (or the Mermaid render, [docs/architecture.png](docs/architecture.png)).

Each component is its own module in [`validator/`](validator); only translation and classification call the model, and every call is cached. A local, semi-deterministic semantic lookup ([`lookup.py`](validator/lookup.py)) decides a line first only when every word of it matches, by meaning, a line a person has reviewed, and no restricted line is close. It never decides a restricted category: anything it is unsure of goes to the model, and the model's unsure lines go to a human. On a blind set of look-alike traps it let none through. On the 32 receipts it decides 1 line (a cold start: its memory holds CORD's Indonesian café lines), and every result is the same as with the model alone. The model's answer is kept as a second opinion, `make lookup-report` checks it, and `mode: shadow` turns it off. After the report, a line needs no person if it is confident and behind no flag; it goes to the review queue (the viewer's Review page) if the model was unsure (EXC-03), a flag or review cites it, it is in the 2% audit of the lookup's decisions, or the lookup and the model disagree. People's decisions there fill the lookup's memory through `make learn` (two keys), one table per client. Its design and tests are experiments 6–12 ([DECISIONS.md, section 12](DECISIONS.md)).

**End to end, RCP-011:** Cellar Door Fine Wines (MCC 5921), Client Entertainment, pre-approval PA-2026-0431: "Vino tinto Rioja Reserva 750ml" is glossed "Rioja Reserva red wine 750ml" and classified `alcohol` (restricted) at 0.99996. Of 12 retrieved rules, the engine raises ALC-03 on L1 (alcohol bought where it isn't served) and MCC-01 (high-risk merchant), which the pre-approval doesn't waive: **flag**, as in the answer key. Each step is in [`outputs/traces.jsonl`](outputs/traces.jsonl).

## Prompts

The classification prompt ([classifier.py](validator/classifier.py)) defines the 21 categories in its own words and says to classify what an item is, not the words in its name, with the merchant as context only. `category` comes before `reason`, so the confidence (the category tokens' probability) measures the answer, not a justification. The translation prompt asks for a literal gloss. Both were tuned only on a 43-item hand-written dev set, by an assistant that had the answer key.

## Policy engine

- **Rules as data, logic as code.** [`rules/rules.yaml`](rules/rules.yaml) holds 40 rules, each with a severity, parameters, `applies_to` and its policy quote; the 34 that can fire each have a small Python check. *Why:* a limit changes with a config edit, as in Expensify or Concur, while each rule's logic stays unit-tested; a rules library is overkill for 34 rules.
- **No rule triggers another, in a fixed order:** regional overrides, then each check, carve-outs, takeovers (TIME-03 over a meal cap, GIFT-04 over GIFT-01), EXC-03, and EXC-01 for the disposition. *Why:* chained rules are what make rule systems hard to debug, and a fixed order makes every outcome traceable.
- **Carve-outs need the right evidence.** Only a recorded reference of the kind a rule names waives it. *Why:* the brief says a missing reference means no carve-out.
- **`Decimal` money, inclusive caps.** *Why:* floating point can misjudge an amount at the cap, and a value at the cap is within it.
- **Quotes stored word for word,** and a test checks each against the policy. *Why:* evidence is graded, and a stored quote can't be invented.
- **Checked:** on the answer key's categories the engine matched all 28 in-scope receipts (the assistant that wrote it had the key to hand), and switching off any one check fails a test ([`test_check_coverage.py`](tests/test_check_coverage.py)).
- **Confidence threshold: 0.5,** pre-registered and held on the dev set (at temperature 1 its one miss scored 0.43; at 0 all 43 are right, the lowest at 0.61): a line below it raises EXC-03 and is left out of every other rule. *Why:* fixed before any output was seen, so it isn't tuned on the test set, and kept low because every EXC-03 signal is scored as wrong.

## Evaluation summary

The labelled receipts are the only test set, so the frozen config's first run was committed before scoring. The classifier now runs at temperature 0, as the brief asks: [experiment 6](experiments/semantic_first/README.md) tested the switch on every test set against a bar set in advance, and no category or decision on the 32 receipts changed. The 28 in-scope receipts, with 95% Wilson intervals:

| | System | Keyword baseline |
|---|---|---|
| Item macro F1 / restricted F1 | 1.000 / 1.000 | 0.738 / 0.791 |
| Receipts exactly right | 28 / 28 (88–100%) | 20 / 28 |
| False positives · missed violations | 0 / 9 (0–30%) · 0 / 16 (0–19%) | 2 / 9 · 2 / 16 |

This proves less than it looks: the traps are the taxonomy's own examples, the assistant that wrote the prompt had seen these receipts, and with nine clean receipts a 30% false-positive rate fits the interval. On 100 real Indonesian receipts ([CORD](cord/README.md)), against the assistant's labels (57 hand-checked), it got 94% of lines right but flagged or reviewed 10 of 98 clean ones (with one restricted line and one made-up context, it tests the classifier more than the rules).

## AI collaboration

Claude Code (Claude Opus 5.5) wrote most of the code, tests and docs; I made the calls and checked the results. [NOTES.md](NOTES.md) logs where I directed, overrode or corrected it. **What it got wrong:** on the first provider, at temperature 0 every chosen token's logprob was 0.0, so the smoke test passed on meaningless numbers until the assistant read the raw values; the classifier ran at temperature 1 until the pinned hosts proved informative at 0. **Checked by hand:** the 11 translations and 57 CORD labels. **Who saw the data:** Anthropic, through the assistant, saw the brief, policies, receipts and answer key. The model APIs (OpenRouter via Fireworks for the submitted run) saw only line text, merchant, MCC description and expense type, plus the public CORD lines; no amounts, employee fields or labels.

## Spend

**$0.0575 over 378 calls** through OpenRouter (`outputs/spend.jsonl`), as billed; a full run costs $0.0058. The temperature-0 calls the committed run replays were made in experiment 6 and billed in its own ledger ($0.0546 with its embeddings); the first run's 350 calls ($0.0759 at peak prices) are in git history. **Model:** DeepSeek V4.1 Flash (`deepseek/deepseek-v4.1-flash`, 1017 release), which I pushed for: with reasoning off it led the open-weights models on Artificial Analysis's index ([archived](https://web.archive.org/web/20260925000203/https://artificialanalysis.ai/models/deepseek-v4-1-flash-non-reasoning)), uses 152 output tokens a receipt and returns the logprobs the confidence needs; reasoning would cost 3.4 times the tokens for nothing this data needs ([why](DECISIONS.md#8-model)). Calls are pinned to Fireworks, then Together, the hosts that returned logprobs.

## What I would do next

Measure flag precision and calibrate the threshold, and the lookup's word threshold (`validator/calibrate.py`), on the company's own receipts; say in the category definitions whether packaging sold with food is `other` (most CORD errors are bags and cups); send names to the model only as placeholders; gloss dish names as the dish, not word by word ("TALAM UNGU" became "PURPLE TRAY").
