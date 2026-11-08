# Experiment 11: version 3.3 of the lookup, tested on look-alike traps (plan)

Written before any embedding or model call on the trap set, and before version 3.3 is built, on the branch `semantic-first-v2`.

## Question

Experiment 10 left two problems:
- **The pipeline's lookup (V3) called a restricted item safe:** "Night cream Soybean" was decided as food because it resembles "Cream [L]".
- **The same-item rule (V3.2) was safe but decided only 3.6% of lines.** Its blocking rule compared restricted references to its own low threshold (0.665), and some restricted line is almost always that close.

Can one version keep V3.2's safety without its over-blocking? CORD's fresh receipts are used up, so safety is tested directly, on restricted items written to look safe.

## The change (version 3.3)

Everything not listed here is V3's, unchanged: the existing code checks, the curated library step (τ_fam 0.84, δ 0.10, margin cutoff 0.108), two-key learning, and restricted categories always going to the model.

1. **A restricted-word check, before any embedding.**
   - **The list:** the project's existing restricted word list (`rules/keywords.yaml`, the restricted categories only). It was frozen for the keyword baseline long before this experiment, so it wasn't written with these traps in view.
   - **The matcher:** the same as the baseline's (`validator/baseline.py`).
   - **A match sends the line to the model.**
2. **Memory recognises only the same item:**
   - similarity of at least τ_same = **0.665**, as calibrated in experiment 10 and not recalibrated;
   - **and** experiment 9's word test: a word Jaccard of at least 0.5, or a set of two or more words inside the other.
3. **A memory match is blocked** (the line goes to the model) if either:
   - **a restricted reference is close:** similarity of at least τ_mem = **0.855**, V3's threshold, not τ_same;
   - **a restricted reference is the same item:** similarity of at least τ_same and passing the word test.
4. **A second vote:** a memory match decides only if the curated library's best category is the same as the memory line's. The best category is the one with the highest similarity to any of its examples, with no threshold. Otherwise the line goes to the model.

## Data

- **Traps** ([traps/](traps/README.md), committed with this plan):
  - 66 restricted look-alikes, aimed at the lookup's memory;
  - 22 safe look-alikes;
  - MCC 5812 throughout.
- **Replay, CORD training R600–R799:** experiment 10's stream, run the same way.
  - Memory starts as the pipeline's and warms up on R400–R599, which isn't scored.
  - Then 200 receipts in batches of 50, learning after each.
  - **These receipts have been seen.** Experiment 10's result on them motivated this design. They estimate how many lines version 3.3 decides. They are not fresh evidence.
- **The project's own sets:** the 32 receipts, the dev set and the reworded receipts. These are a regression check.

## Arms

- **M:** the model on every line.
- **V3:** the pipeline's lookup as it is.
- **V3.2:** experiment 10's same-item rule.
- **V3.3:** all four changes.
- **Ablations, reported with no bar:**
  - **V3.3 without the word list:** how safe the embedding rules are alone;
  - **V3.3 without the second vote.**

**Two memories for the traps:**
- **The pipeline's committed memory:** 813 lines and 14 risk references.
- **The memory after the replay:** learned from 1,000 receipts. More reviewed lines means more look-alikes, so this is the harder case.

## Bar (fixed now)

Version 3.3 replaces V3 in the branch's pipeline only if all of these hold:
1. **The project's own sets, V3.3 with the committed memory:**
   - 28/28 receipts exactly right, 65/65 items, 0/9 false positives, 0/16 missed violations;
   - all 4 timing receipts match;
   - the dev set at least 42/43;
   - the reworded receipts no worse than M.
2. **Safe on the traps, with both memories:**
   - the lookup decides **none** of the 66 restricted traps as a safe category;
   - the system misses no more restricted traps than M.
3. **Safe and accurate on the replay:**
   - no restricted item with a sure label decided as safe by the lookup;
   - the lookup's decisions on sure labels at least 99.5% right;
   - lines right at least M's minus 2;
   - false alarms no more than M's;
   - missed violations no more than M's.
4. **Useful on the replay:** the lookup decides at least 35% of lines, the same bar as experiments 8 to 10.
5. **Repeatable:** identical vectors re-embedded in a fresh process, to 1e-5.

**Reported with no bar:**
- every arm on the traps: restricted traps decided safe, and safe traps decided or sent to the model;
- the ablations;
- how many lookup decisions each new rule blocked;
- receipts needing no model call.

Nothing is tuned on the traps or the replay. A line that the code wrongly treats will be reported, not fixed in this run.

**If version 3.3 passes:**
- it goes into the branch's pipeline (`rules/lookup/config.yaml` and `validator/lookup.py`);
- the outputs are regenerated, and the docs and viewer follow;
- **it does not go to main on this evidence.** Main needs fresh receipts the design hasn't seen, ideally a company's reviewed ones, or a shadow-mode run in which the lookup only logs.

**If it fails,** V3 stays in the branch's pipeline. The write-up will put to the user whether to return the branch to the model on every line.

## Spend

- **Model calls:** the traps, about $0.01, in this experiment's `spend.jsonl`. The replay and the project's sets replay from the cache.
- **Embeddings:** local, $0.

## What this can't show

- **The traps were written by the assistant, who knew the design.** They were aimed at the looseness found in experiments 9 and 10, but a writer can't attack a rule they don't think of.
- **The replay receipts aren't fresh,** so the usefulness figure is an estimate.
- **The labels are the assistant's,** not checked by hand.
- **The word list catches 17 of the 66 restricted traps by itself** (checked before committing, without any model). The ablation shows how the other rules do without it.
