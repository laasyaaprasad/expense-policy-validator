# Experiment 9: a stricter "same item" rule for the lookup's memory (plan)

Written before any embedding or model call on the new receipts, on the branch `semantic-first-v2`.

## Question

Wiring version 3 into the pipeline showed that its memory threshold (similarity 0.855) admits **similar** items, not only repeats:
- "Grilled salmon entree" was decided from the reviewed "Chicken Steak" (0.925);
- "\*Rhum", a rum flavouring, from "BANANA RHUM RAISIN" (0.90), with the nearest restricted reference a cigar at 0.81.

The cause is the calibration rule. "Pairs above the threshold agree on category" is easy when 93% of CORD lines are food. Does a stricter rule keep version 3's safety and accuracy while deciding only true repeats, and at what cost in lines decided?

## The change (version 3.1)

Memory decides a line only if the reviewed line is **the same item**, which now needs both:
1. **Close in meaning:** similarity of at least τ_same.
2. **The same words:** the normalised word sets (lower case, runs of letters, numbers ignored) overlap by a Jaccard index of at least 0.5, or one set contains the other. Under this rule:
   - "ES TEH MANIS" / "teh manis" passes (0.67).
   - "\*Rhum" / "BANANA RHUM RAISIN" fails (0.33).
   - "Grilled salmon entree" / "Chicken Steak" fails (0).

A restricted reference blocks a memory decision if it is the same item as the line by the same test, or if its similarity is at least τ_same. Everything else is version 3's unchanged: the code checks, the curated library's τ_fam = 0.84, δ = 0.10 and margin cutoff 0.108, two-key learning, restricted categories always to the model.

**τ_same, calibrated before any call on the new receipts:**
- **Data:** every sure-labelled line of the 600 receipts already used (CORD validation, test, and training R000–R399).
- **Pairs:** those on different receipts that pass the word test.
- **Rule:** the lowest similarity at which **every** such pair at or above it agrees on category (100%, not 99.5%), with no restricted-flag disagreement above it.

## Amendment, before any embedding or model call on the new receipts

As written, "or one set contains the other" lets a single word count as the same item: {rhum} sits inside {banana, rhum, raisin}. That contradicts the example above, which says "\*Rhum" fails. **Amended:** containment counts only when the smaller word set has at least two words. "teh manis" in "ES TEH MANIS" still passes, and "\*Rhum" in "BANANA RHUM RAISIN" now fails (Jaccard 0.33). Nothing had been run on the new receipts when this was changed.

## Arms, on the 200 fresh receipts (CORD training R400–R599)

The stream continues from where experiment 8 ended: memory starts as `rules/lookup/memory.csv` (813 lines and 14 risk references from R000–R399) and keeps learning after every 50 receipts, under the two-key rule.
- **M:** the model on every line (main's classifier).
- **V3:** the pipeline's current lookup, with memory threshold 0.855 and no word test.
- **V3.1:** the stricter rule.

**Also run:** V3.1 with its memory on the 32 receipts, the dev set and the reworded receipts, against M.

## Bar (fixed now)

V3.1 replaces V3 in the branch's pipeline only if all of these hold:
1. **The project's own sets:**
   - 28/28 receipts exactly right, 65/65 items, 0/9 false positives, 0/16 missed violations;
   - all 4 timing receipts match;
   - the dev set at least 42/43;
   - the reworded receipts no worse than M.
2. **Safe on the fresh receipts:**
   - no restricted item with a sure label decided as safe by the lookup;
   - the lookup's decisions on sure labels at least 99.5% right.
3. **As accurate as M on the fresh receipts:**
   - lines right at least M's minus 2;
   - false alarms no more than M's;
   - missed violations no more than M's.
4. **Still useful:** the lookup decides at least 35% of the fresh lines.
5. **Repeatable:** identical vectors when re-embedded in a fresh process, to 1e-5.

**Reported with no bar:**
- V3 against V3.1: lines decided, errors, and memory decisions whose matched line fails the word test;
- receipts needing no model call;
- the calibrated τ_same.

Nothing is tuned on what the fresh receipts show.

## Spend

- **Model calls:** M on 200 receipts, about $0.06, in this experiment's `spend.jsonl`.
- **Embeddings:** local, $0.

## What this can't show

- **The word test is a proxy for "the same item".** Different items can share words ("chicken katsu curry" and "chicken katsu"), and the same item can be written without shared words, as in another language.
- **CORD repeats more than a company's receipts would.**
- **The labels are the assistant's,** not checked by hand.
