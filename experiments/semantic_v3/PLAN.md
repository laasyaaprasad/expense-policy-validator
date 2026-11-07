# Experiment 8: recognise first, generalise carefully, the model as fallback, at scale (plan)

Written before any embedding or model call on the new receipts, on the branch `semantic-first-v2`. Main is not changed by anything here.

## Question

As more receipts arrive and are reviewed, does a semi-deterministic classifier take over a growing share of lines from the model, without ever calling a restricted item safe? This is the question that decides whether it beats sending every line to the model at scale. Experiment 7 answered half of it:
- **Learning lifted the lookup from 6% to 67% of lines.**
- **But it then decided "MAKOLI" (makgeolli) was food.** Its best match was "milo", a chocolate drink, matched by spelling at 0.86: the small model doesn't know the word. No restricted library line was near it, so no rule based on "what is it near?" could have caught it.

## Design: four steps, the model only as fallback

**Step 0: code checks**, as in experiment 7. The line goes to the model if:
- it may name a recipient;
- it may be a phone, prescription or event ticket;
- it has no real words;
- the merchant is a package store, pharmacy or supermarket.

**Step 1a: memory, the part that grows.** A line is decided from a reviewed line only if all of these hold:
- the line is a **near-repeat** of the reviewed line: similarity of at least τ_mem;
- the reviewed line's category is **unrestricted**;
- **no restricted reference line** (curated or reviewed) is also at τ_mem or more.

A line is added to memory after its batch is reviewed only with **two keys**: its label is sure (the reviewer is confident) **and** the model, on the same receipt, gave the same category at 0.9 or more. Restricted reviewed lines with a sure label are always added, as **risk references**: they never decide anything, they only block.

**Step 1b: curated generalisation.** Only the curated library (experiment 7's, decontaminated against every test line, the new ones included) may generalise, and only to **unrestricted** categories that don't need the model (not `other`, `health_otc` or `gift`). It decides only if all of these hold:
- **familiar:** the best curated similarity is at least τ_fam (an unfamiliar line goes to the model);
- **clear:** the margin over the next category is at least the safe cutoff, chosen on the curated library leave-one-out as in experiment 7;
- **far from risk:** the best similarity minus the nearest restricted reference's (curated or reviewed) is at least δ.

Learned lines never generalise; they only recognise near-repeats.

**Step 2: the model**, as on main: temperature 0, the whole receipt, its own confidence. **Step 3:** below 0.5, a human (EXC-03).

**Restricted categories are never decided by the lookup.** The model decides every restricted line.

## Calibration, fixed now, on data the stream doesn't use

τ_mem, τ_fam and δ are chosen on CORD validation and CORD test: 200 receipts with labels, seen before. The stream never contributes to them.
- **τ_mem:** over every pair of sure-labelled lines on different receipts, the lowest similarity at which pairs at or above it agree on category at least 99.5% of the time, with no pair disagreeing on the restricted flag.
- **τ_fam and δ:** step 1b is run on every calibration line, over a grid (τ_fam from 0.70 to 0.95 in steps of 0.01; δ from 0.00 to 0.20 in steps of 0.01). The pair chosen decides the most lines, among those whose decided lines are at least 99.5% right on sure labels with **no restricted item decided as safe**. On a tie, the larger τ_fam, then the larger δ.

## The stream (the test)

**CORD's training split, 400 fresh receipts, in their order, in 8 batches of 50.** It starts cold: no memory. Each batch is first classified by the system as it stands (steps 0–3). Then its lines are "reviewed": the labels stand in for a human reviewer, and memory is updated by the two-key rule before the next batch.

**Main** (M, the model at temperature 0 on every line) is run on the same 400 receipts for comparison. The lookup's lines take M's answer on the same receipt where they escalate.

**Also reported:**
- **the curated-only system** (no memory) on the 32 receipts, the dev set and the reworded receipts, against M;
- **the share of lookup decisions the model disagrees with,** the production spot-check, here measured on every decided line.

## Bar (fixed now)

V3 counts as better than sending everything to the model at scale only if all of these hold:
1. **Curated-only, on the project's own sets:**
   - 28/28 receipts exactly right, 65/65 items, 0/9 false positives, 0/16 missed;
   - all 4 timing receipts match;
   - dev set at least 42/43;
   - reworded receipts no worse than M.
2. **Safe across the whole stream:**
   - **no restricted item with a sure label decided as safe by the lookup**;
   - the lookup's decisions on sure labels at least 99.5% right.
3. **As accurate as main on the stream:**
   - lines right at least M's minus 3 (of 1,212);
   - false alarms no more than M's;
   - missed violations no more than M's.
4. **It grows:**
   - the lookup decides at least 40% of the lines in the last 100 receipts;
   - its share in each block of 100 receipts is at least the share in the block before.
5. **Repeatable:** identical vectors when re-embedded in a fresh process, to 1e-5.

**Reported with no bar:**
- receipts needing no model call, and translations avoided, per block;
- the model's disagreement rate on lookup decisions;
- the calibrated τ_mem, τ_fam and δ.

Nothing is tuned on what the stream shows.

## Spend

- **Model calls:** M on 400 receipts, about 400 classifications and the translations: roughly $0.11, in this experiment's `spend.jsonl`.
- **Embeddings:** local, $0.

## What this can't show

- **CORD is Indonesian cafés, bakeries and a few shops,** so receipts repeat more than a company's mixed expenses would. The stream shows that the mechanism works and stays safe, not the share a company would reach.
- **The labels stand in for human review,** and they are the assistant's, not yet checked by hand.
- **One synthetic context (MCC 5812) on every CORD receipt,** so the merchant check never fires in the stream.
- **The 400 receipts are one ordering of one sample;** the growth curve is a single draw.
