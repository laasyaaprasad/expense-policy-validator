# Experiment 12: every word accounted for, by meaning (plan)

Written before the rule is built and before any embedding or model call on the new trap set, on the branch `semantic-first-v2`.

## Question

Experiment 11 showed how look-alikes get through: a restricted item adds a word that nothing in the reviewed line explains.
- "**Makgeolli** Original" was decided from "ORIGINAL".
- "**Arak** Bali" from "Nasi Campur ala Bali".
- "Bintang **Radler** Lemon" from "Lemon".
- "**Night** cream **Soybean**" from "Cream [L]".

Can a lookup that still matches by meaning, but only when every word on both sides is accounted for, catch every trap? And does it decide more lines than exact text matching would?

## The rule (version 4)

For each line of an itemized receipt, in order:
1. **The existing code checks.** A possible recipient; a phone, prescription or event ticket; no real words; or a merchant where context decides. Any of these sends the line to the model, unchanged.
2. **Words:**
   - **What counts:** the line's runs of letters, lower-cased, of two or more letters.
   - **Left out:** a fixed list of units: ml, gr, kg, pcs, pc, pkt, ltr, oz, cl. Single letters such as size codes ("[L]") are left out by the two-letter rule.
   - **No words left** sends the line to the model.
3. **Memory match, every word accounted for:**
   - **The candidates:** the 20 reviewed lines closest to the line by sentence similarity, using the same embedding model as before.
   - **The test:** a candidate matches only if **every word of the line** has a word in the candidate with word similarity of at least τ_word, **and every word of the candidate** has one in the line.
   - **Word similarity:** single words embedded by the same model, with the same instruction.
   - **The decision:** the closest candidate that matches gives the line its category.
4. **Restricted references block,** checked before deciding. Either of these sends the line to the model:
   - any restricted reference with sentence similarity of at least 0.855 (V3's threshold);
   - any restricted reference that matches the line by the same word test (step 3).

   The restricted references are the reviewed restricted lines plus the library's restricted examples, as before.
5. **Otherwise the line goes to the model.** The curated library no longer decides anything. After that, as now, the model's unsure lines (confidence below 0.5) go to a person.

**Learning is unchanged:** two keys for safe lines, and reviewed restricted lines become restricted references.

## τ_word, fixed before any call on the new traps

Two parts. τ_word is the higher of the two.
1. **A floor from the frozen word list:**
   - **The comparison:** the single-word terms of `rules/keywords.yaml` (frozen for the keyword baseline), restricted terms against unrestricted ones.
   - **The rule:** τ_word must be above the highest word similarity between a restricted term and an unrestricted term. Plus 0.01, so that "wine" can never count as "juice".
2. **Calibration on the 800 CORD receipts already used:**
   - **The data:** validation, test, and training R000–R599, sure labels only.
   - **The pairs:** lines on different receipts with sentence similarity of at least 0.5.
   - **The rule:** the lowest τ_word at which the pairs passing the word test agree on category at least 99.9% of the time, with no restricted-flag disagreement. This is experiment 10's rule.

## Data

- **The blind trap set** ([traps/](traps/)):
  - **Who wrote it:** a fresh agent that was not shown the design, the lookup's code, its memory or the CORD receipts. It saw only the category list and a description of the receipts.
  - **What it holds:** after the assistant's label check, **57 restricted look-alikes and 22 safe ones** on 20 receipts. Six lines were dropped first: one ambiguous label, and five copies of lines the design has seen (listed in [traps/README.md](traps/README.md)).
  - **When it was committed:** with this plan, before any model or embedding saw it.
  - **The receipts:** MCC 5812 and no merchant name, so the merchant gives no help.
- **Experiment 11's trap set:** 66 restricted and 22 safe lines. This rule was designed after seeing it, so it is a regression check, not evidence.
- **The replay:** experiment 10's stream. The memory starts as the pipeline's, warms up on R400–R599, then streams R600–R799 in batches of 50, learning after each. These receipts have been seen.
- **The project's own sets:** the 32 receipts, the dev set and the reworded receipts.

## Arms

- **M:** the model on every line.
- **V3:** the pipeline's lookup as it is.
- **A:** exact word sets, with no embeddings. The same words as step 2, compared as sets, with the same restricted-reference rule but by exact word set only. This is the non-semantic baseline.
- **V4:** the rule above.
- **V4 + risk words, reported with no bar:** V4, plus any line with a word that matches (by τ_word) a word of a reviewed restricted line goes to the model.
  - **Why it isn't part of V4:** the reviewed restricted lines include generic words such as "besar" (large), "cream", "home" and "baby". Those would send a great deal of ordinary food to the model.
  - **What V4 already does:** its restricted-reference check (step 4) catches a line whose words match a restricted line. That is the targeted form of the same idea.
  - **What the arm shows:** what risk words would add.

The traps are run with the committed memory and with the memory after the replay, as in experiment 11.

## Bar (fixed now)

V4 replaces V3 in the branch's pipeline only if all of these hold:
1. **The project's own sets:**
   - 28/28 receipts exactly right, 65/65 items, 0/9 false positives, 0/16 missed violations;
   - all 4 timing receipts match;
   - the dev set at least 42/43;
   - the reworded receipts no worse than M.
2. **Both trap sets, both memories:**
   - the lookup decides **no** restricted trap as a safe category;
   - the system misses no more restricted traps than M.
3. **The replay:**
   - no restricted item with a sure label decided as safe;
   - decisions on sure labels at least 99.5% right;
   - lines right at least M's minus 2;
   - false alarms no more than M's;
   - missed violations no more than M's.
4. **Meaning adds something:** V4 decides more replay lines than A.
5. **Repeatable:** identical vectors re-embedded in a fresh process, to 1e-5.

**The usefulness bar changes from experiments 8 to 11 (35% of lines), stated now, before any result:**
- **The user's priority is now missing no trap,** and cost comes second. The model costs about $0.00017 a receipt.
- **So the question is whether semantic matching earns its place over exact matching,** not whether it reaches a fixed share.
- **The share is reported against 35%** for comparison.

**Reported with no bar:** each arm's share of lines decided, the traps each arm called safe, the calibrated τ_word and its floor, and which rule sent lines to the model.

Nothing is tuned on the traps or the replay.

**If V4 passes:**
- it replaces V3 in the branch's pipeline (`rules/lookup/config.yaml`, `validator/lookup.py`);
- the outputs are regenerated, and the viewer and docs follow;
- **it does not go to main on this evidence.** Main needs fresh receipts the design hasn't seen, or a shadow run.

**If it fails,** the write-up recommends returning the branch to the model on every line.

## Spend

- **Model calls:** the blind traps, about $0.01. Everything else replays from the cache.
- **Embeddings:** local, $0.

## What this can't show

- **The blind traps were written by a model,** not a person, and labelled by the assistant.
- **The replay receipts aren't fresh.**
- **A word-level threshold is still a threshold.** Two different items whose words are all near-synonyms could match.
