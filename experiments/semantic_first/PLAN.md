# Experiment 6: a semantic first stage, then the model at temperature 0 (plan)

Written before any embedding or model call. The submitted system is not changed by anything here. If the result passes, replacing the classifier is a separate change, reviewed on its own.

## Question

Can a semi-deterministic classifier replace the current one: a semantic stage that decides the lines whose meaning is clear and safe, with every other line escalated to the model at temperature 0? It must keep every result the current classifier reached, and decide a useful share of lines without the model. The brief asks for temperature 0 "where you can". The classifier runs at 1 only because the first provider's logprobs were empty at 0 (DECISIONS.md, section 4); the pinned hosts return real ones at 0.

## Design

### Stage 1: semantic (no model call to the LLM; an embedding per line)

- **Anchors** (`anchors.csv`): 391 short lines, each a plain, typical example of one of the 21 categories, in English and the dataset's other languages. Each is marked with its source: a term the taxonomy or the prompt's definitions name (`definition`), one of the prompt's own rule examples (`prompt rule`), or a typical item (`typical`). They were drafted by the assistant, which has seen the receipts, so there are **no examples written for traps**: misleading names are left to the guards below, which send them to the model. The anchors are fixed by this commit and not yet hand-checked. Any change after the results is a new version, and has to be tested on data none of this run used (CORD's test split).
- **Decontamination, before any call:** an anchor is dropped if it is a near-copy of any line in the test sets (the 32 receipts, the dev set, the 64 reworded lines, the 270 CORD lines). Near-copy means the same normalised text, a word overlap (Jaccard) of 0.5 or more, or a character similarity (difflib ratio) of 0.8 or more. Dropped anchors are listed in the results.
- **Embedding model**, through OpenRouter, pinned to DeepInfra, each vector cached so reruns are offline. The three candidates all have open weights, so production could run them locally:
  - `intfloat/multilingual-e5-large`, with the prefix `query: `;
  - `baai/bge-m3`, with no prefix;
  - `qwen/qwen3-embedding-8b`, with one instruction on both sides.

  **The choice is made on the anchors alone:** the highest leave-one-out accuracy, where each kept anchor takes the category of its nearest other anchor. On a tie, bge-m3. The line text is normalised (`validator/normaliser.py`) and embedded as it is, in its own language, with no translation.
- **Scores:** cosine similarity to every anchor. A category's score is its nearest anchor's similarity. The line takes the best category, and its **margin** is the best score minus the second best.
- **The stage decides a line only if all five hold:**
  1. The margin is at least the cutoff τ.
  2. **Restricted guard:** the 10 nearest anchors all have the same restricted flag as the chosen category. So a line near both food and alcohol, or near a mug and a cocktail, always goes to the model.
  3. The category is not `other`, `health_otc` or `gift`. Those need what only the model returns: an item kind (phone, prescription, event ticket), an explanation, or a recipient.
  4. **No text gate fires.** The gates look for:
     - **a recipient:** a title with a name, an initial with a surname, "for" with a capitalised name, or "gift", "present" or "client" in the dataset's languages;
     - **an item kind:** phone words, prescription words, and venue words (golf, concert, theatre, match, stadium, admission). Also "ticket" with no transport word beside it.
  5. The receipt is itemized. A non-itemized line stays `other` in code, as now.
- **Cutoff τ, chosen on the anchors alone:** every kept anchor is decided leave-one-out by the same rule. The candidates are 0 and every distinct leave-one-out margin. τ is the lowest candidate at which the anchors decided are at least 99% right with no restricted-flag error. If none qualifies, the stage decides nothing.
- **Confidence of a decided line:** of the anchors decided leave-one-out with a margin at least this line's, the share that were right, smoothed: (right + 1) / (decided + 2).
- **What a decided line returns:** the category, `restricted` from the taxonomy, the confidence, no item kind, no recipient, and a reason naming its three nearest anchors.

### Stage 2: the model, at temperature 0

- **The same prompt (`classify-v1`), model and hosts as now.** The only change is the temperature, and every call is fresh, in this experiment's own cache. Nothing from the committed run is reused, translations included.
- **Gate before the arms run:** the dev set is classified at temperature 0. Its logprobs count as informative if at least one line's confidence is below 0.9999 and at least one category alternative has a probability above 1e-6. If they don't, arms B and C use temperature 1 instead, as fresh calls, and the write-up says so.
- The model gets the whole receipt, as now. In arm C its answers are used only for the lines the semantic stage didn't decide. A receipt whose every line was decided needs no model call, and is counted as such.
- **The EXC-03 threshold stays 0.5** (DECISIONS.md, section 4). It is not re-tuned on anything this run produces.

### Stage 3: review

Unchanged: a line below 0.5 raises EXC-03.

## Arms

- **A, the submitted system (temperature 1):** read from the committed outputs, for comparison only. Nothing of it is an input to B or C.
- **B, the model only, at temperature 0 (or 1, if the gate fails):** this shows what the temperature alone changes.
- **C, the semantic stage, then B's answers for every line it didn't decide:** the proposal.

## Test sets: everything run so far

1. **The 32 receipts.** All of them go through the engine, since cross-receipt rules need them. Scored on the 28 by the Part 3 measures; the 4 timing receipts are compared with their answers.
2. **The dev set** (14 receipts, 43 trap items), now a test set: it chooses nothing.
3. **The reworded receipts** (experiment 5's checked variants), with the 4 timing receipts from the same arm.
4. **CORD**, 100 real receipts, scored against `cord/labels.jsonl` as in `evaluation/cord.py`.
5. **Stability:** a fresh repeat of B on the 32 and the dev set.
6. **The merchant ablation:** B without the merchant and expense type, on the 32.

## Bar (fixed now)

**B and C each pass only if all of these hold:**
1. On the 28 receipts: 28/28 exactly right, 65/65 items, 0/9 false positives, 0/16 missed violations.
2. All 4 timing receipts match their answers.
3. Dev set: at least 41/43 categories, at least 42/43 item kinds and at least 42/43 recipients right. A had 42, 43 and 43.
4. Reworded receipts: at least 62/65 items and 26/28 receipts exactly right, the bar of experiment 5.
5. CORD: at least 248/270 lines right, at most 13/98 false alarms, and both violations flagged.
6. The fresh repeat changes no category on the 32 receipts or the dev set.

**C must also meet both of these:**

7. **Safe:** across every labelled line (the 32 receipts, the dev set, the reworded lines and CORD), the lines the semantic stage decides are at least 98% right, with no restricted-flag error.
8. **Useful:** the semantic stage decides at least 25% of the lines on the 28 receipts and CORD together (at least 84 of 335). Below that, the stage is semi-deterministic in name only.

The merchant ablation, the number of receipts that need no model call, and the cost are reported, with no bar. If C passes everything but 8, the result is "safe but not useful". Nothing is tuned on what this run shows.

## Spend

About 300 model calls (classify and translate, in arm B, its repeat and the ablation) and about 2,500 embeddings across the three candidates: roughly $0.05–0.10, all in this experiment's `spend.jsonl`.

## What this can't show

- **The anchors were written by an assistant that has seen the test sets.** Decontamination catches copies, not near-misses in meaning. Leaving out trap examples limits the leak, but doesn't remove it.
- **The 28 receipts were seen while the system was built,** so the evidence that counts is the reworded lines and CORD.
- **The embeddings come from a hosted API here.** Locally, the same open model would make the stage fully offline and deterministic. That is a choice for the merge.
- **DeepInfra** (embeddings) receives the anchors and the line text of every test set. **Fireworks or Together** (classification) receive what they receive now. Neither gets amounts, employee fields or labels.
