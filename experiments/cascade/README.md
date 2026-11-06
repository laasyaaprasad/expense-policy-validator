# Experiment 1: a cascade classifier

**Question:** can a cheap, repeatable first stage label receipt lines by meaning and send only the uncertain ones to the LLM, keeping the LLM-only system's accuracy at lower cost? The design, the threshold rule and what would count as success were written down first, in [PLAN.md](PLAN.md). Run it with `make cascade`: offline, from the cached embeddings and the committed LLM answers. Full numbers: [results/summary.md](results/summary.md), [results/metrics.json](results/metrics.json), per line in [results/lines.csv](results/lines.csv).

**Answer: not on this data.** The embedding cascade matched the LLM-only system exactly, but only by escalating almost everything: it saved about 9% on the 28 receipts and nothing on real receipts, and the one real-receipt line it kept made a decision worse.

## Results

**28 scored receipts** (65 lines; costs at list price, $ per receipt):

| Arm | Exactly right | False positives · missed | Lines to the LLM | Receipts still calling the LLM | $ / receipt |
|---|---|---|---|---|---|
| LLM only (submitted) | 28 / 28 | 0 / 9 · 0 / 16 | 64 / 64 | 27 / 28 | 0.000350 |
| Embedding cascade | 28 / 28 | 0 / 9 · 0 / 16 | 52 / 64 | 26 / 28 | 0.000319 |
| Embedding cascade, conservative | 28 / 28 | 0 / 9 · 0 / 16 | 53 / 64 | 26 / 28 | 0.000321 |
| Embedding only | 19 / 28 | 2 / 9 · 3 / 16 | 0 | 0 | ≈ 0 |
| TF-IDF cascade | 27 / 28 | 1 / 9 · 0 / 16 | 51 / 64 | 26 / 28 | 0.000317 |
| TF-IDF only | 12 / 28 | 6 / 9 · 2 / 16 | 0 | 0 | 0 |

**100 real receipts (CORD):** the embedding cascade escalated 268 of 270 lines. Lines right stayed at 250 / 270 and false alarms at 13 / 98, but one review became a false flag (flag precision 2 / 8 → 2 / 9).

## What it shows

1. **The cheap stage is only confident on the obvious.** The 12 receipt lines it kept were gratuities, service charges, cables and adapters, a room charge and a greeting card, all right. Embedding margins are small (median 0.022 on the receipts, 0.007 on CORD), and the dev-chosen threshold (0.051) keeps only the clearest lines.
2. **Keeping a line rarely saves a call.** Each classify call costs a fixed ~980-token prompt plus ~34 input and ~56 output tokens a line (fitted from 131 calls in the spend ledger). Escalating 81% of lines left 26 of 28 receipts still calling the LLM, so the saving was 9%, not 19%.
3. **On real receipts it does nothing.** Indonesian dish names and abbreviations ("SB 1 OR", "TALAM UNGU") sit far from every prototype, so 268 of 270 lines escalated.
4. **Keeping a line throws away the LLM's doubt.** The LLM called CORD's "Bag-S" clothing at 0.23, so EXC-03 sent that receipt to review. The cascade kept the same answer at full confidence, and PERS-02 flagged it. A cascade needs its own calibrated confidence, or it turns uncertain lines into confident flags.
5. **Embeddings alone are no better than keywords.** 19 / 28 receipts against the keyword baseline's 20 / 28: the traps (beer-battered cod, rum cake) sit close to alcohol in embedding space too.
6. **Spelling is not meaning.** TF-IDF matched "Cafe expresso" to laundry (via "pressing") at a high margin, so its dev threshold didn't carry over, and it got RCP-008 wrong. The conservative variant made it worse (25 / 28).

## Where a cheaper first stage could pay

Untested ideas, from what the cost model shows:
- **An exact-match cache of normalised line text.** Gratuities and service charges were 7 of the 64 receipt lines. At 50,000 receipts a day, repeated lines could skip the LLM deterministically, with the answer and confidence first given by the LLM.
- **Batching several receipts per call,** to spread the fixed ~980-token prompt. Fireworks' prompt caching already does part of this: the billed cost was $0.00019 a receipt against $0.00035 at list price.

## Limits

- 43 dev items chose the threshold, so it is noisy. The 28 receipts were seen while building the system, and CORD's labels are the assistant's (57 hand-checked).
- Escalated lines reuse the LLM's committed answers, made with the whole receipt in view, which slightly flatters the cascade.
- One embedding model (`intfloat/multilingual-e5-large`) and simple prototypes (category definitions, their listed terms, the dev items); a fine-tuned or larger model might do better, but would need labelled data.
- Costs are at Fireworks' list price, not as billed.

## Spend and data

508 embeddings for $0.000053 through OpenRouter (`spend.jsonl`, kept apart from `outputs/spend.jsonl`), plus a $0.00000018 test call. **DeepInfra**, OpenRouter's only host for the model, received the line text of the receipts, the dev set and CORD, and the category definitions; no amounts, employee fields or labels.
