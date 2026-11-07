# Experiment 10: the same-item rule with a tolerant calibration (plan)

Written before any embedding or model call on the new receipts, on the branch `semantic-first-v2`.

## Question

Experiment 9's same-item rule was safe, but its threshold was forced to 1.0 (exact text) by a single mislabelled pair: R256's "TAKE AWAY CHARGE". That label has since been corrected (`cord/train/README.md`). With a calibration that one bad label can't decide, is the same-item rule safe, as accurate as the model, and useful enough to replace the pipeline's looser memory rule?

## Design

**Version 3.2:** experiment 9's same-item rule, unchanged.
- **The rule:** similarity of at least τ_same, **and** the same words. A word Jaccard of at least 0.5, or a set of two or more words inside the other.
- **Restricted references block,** as before. Everything else is the pipeline's lookup as committed.

**τ_same, calibrated before any call on the new receipts:**
- **Data:** every sure-labelled line of the 800 receipts used so far (CORD validation, test, training R000–R599), with the R256 correction.
- **Pairs:** those on different receipts that pass the word test.
- **Rule:** the lowest similarity at which the pairs at or above it agree on category at least **99.9%** of the time, with **no** restricted-flag disagreement at or above it.

**The stream, as production would run:**
- **Warm-up:** memory starts as the pipeline's (learned from R000–R399), then learns R400–R599 by the same two-key rule. That gives a library learned from 600 reviewed receipts. The warm-up isn't scored.
- **The test:** the 200 fresh receipts R600–R799, in batches of 50, learning after each.

**Arms:**
- **M:** the model on every line.
- **V3:** the pipeline's lookup as it is (memory within similarity 0.855, no word test).
- **V3.2:** the same-item rule with the tolerant τ_same.

**Also run:** V3.2 with the pipeline's committed memory on the 32 receipts, the dev set and the reworded receipts.

## Bar (fixed now, the same as experiment 9's)

V3.2 replaces V3 in the branch's pipeline only if all of these hold:
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
4. **Useful:** the lookup decides at least 35% of the fresh lines.
5. **Repeatable:** identical vectors re-embedded in a fresh process, to 1e-5.

**Reported with no bar:**
- V3 against V3.2 on the same stream;
- memory decisions failing the word test;
- receipts needing no model call;
- τ_same and the pairs behind it.

Nothing is tuned on the fresh receipts.

**If V3.2 passes, adopting it means:**
- `memory_rule: same_item` and the calibrated τ_same go into `rules/lookup/config.yaml`;
- the pipeline's outputs are regenerated;
- the docs and viewer follow.

If it fails, V3 stays.

## Spend

The model on the 200 fresh receipts, and on the warm-up receipts where the cache misses: about $0.06. Embeddings are local.

## What this can't show

- **These are CORD's last fresh receipts.** After this run there is no unseen CORD data left.
- **The labels are the assistant's,** not checked by hand. A tolerance of 99.9% allows about one disagreement in a thousand pairs.
- **The word test is a proxy for "the same item".**
