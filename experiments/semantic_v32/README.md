# Experiment 10: the same-item rule with a tolerant calibration

**Question:** with a calibration that one bad label can't decide, is the same-item rule safe, as accurate as the model, and useful enough to replace the pipeline's looser memory? The plan and the bar were committed before any call ([PLAN.md](PLAN.md)), as were CORD's last 200 fresh receipts and their labels ([cord/train3](../../cord/train3/README.md)).
- **Run it with** `make semantic-v32`.
- **Results:** [results/metrics.json](results/metrics.json), [results/stream.csv](results/stream.csv).

**Answer: no, and the pipeline's own lookup failed its safety test here.**
- **The same-item rule (V3.2)** was safe but decided only **3.6%** of lines, against a 35% bar. The tolerant calibration put its threshold at 0.665, and at that level the planned blocking rule ("any restricted reference at least as similar blocks") blocks nearly every line.
- **The pipeline's current lookup (V3) called a restricted item safe:** "Night cream Soybean", a skincare product, was decided as food because it resembles "Cream [L]", a drink-size option. That made the system **miss a violation the model alone caught**: 5 missed against the model's 4.

**On these receipts, the looser memory is unsafe and the stricter one isn't useful.**

## Results on the last 200 fresh receipts (CORD training R600–R799, 635 lines)

Memory warmed up on R400–R599 first (not scored), so both arms started from 600 reviewed receipts.

| | M · the model on every line | V3 · the pipeline's lookup | V3.2 · same-item rule |
|---|---|---|---|
| Lines right | 599 | **608** | 599 |
| False alarms (of 184 clean) | 9 | 8 | 9 |
| **Missed violations (of 16)** | **4** | **5** | **4** |
| Lines decided by the lookup | | 416 (66%) | 23 (3.6%) |
| Its decisions right, sure labels | | 409 / 410 | 23 / 23 |
| **Restricted items called safe** | | **1** ("Night cream Soybean", via "Cream [L]") | 0 |
| Memory matches failing the word test | | 125 of 415 | 0 |
| Receipts needing no model call | | 94 | 4 |

**The other measures:**
- **The project's own sets, V3.2 with the committed memory:** identical to M (28/28, 65/65, dev 43/43, reworded 64/65 and 27/28).
- **Repeatable:** 4.6 × 10⁻⁷.
- **Spend:** $0.064.

## Calibration

- **τ_same = 0.665:** 4,760 of the 4,761 same-word pairs above it agree.
- **The one that doesn't is "BCA PROMO"**, labelled `other` on one receipt and `food_beverage` on R426. That is another labelling error by the assistant: a bank-card discount is `other` by the guide. The 99.9% tolerance absorbed it, as it was meant to.
- **The cost of the tolerance:** the threshold fell to 0.665. The plan's blocking rule then compares the nearest restricted reference to that same threshold, and in this embedding space some restricted line is almost always above 0.665. So V3.2 sent nearly every line to the model.

## What this shows

1. **The looser memory (V3, in the branch's pipeline) is not safe enough.** Across experiments 8, 9 and 10 it decided over 1,000 fresh lines, made one restricted error, and the system missed one more violation than the model on these receipts. It can't go to main as it stands.
2. **Requiring the same words stops resemblance errors:** V3.2's matches never failed the word test, and it made no error. But a blocking rule written for a high threshold turns off almost everything at a low one. That is a design flaw found by this run, so it can't be fixed and judged on these receipts.
3. **Labels need checking:** two of the assistant's labels turned out wrong, both on charges and discounts. A tolerant calibration copes with that; a 100% rule doesn't.
4. **CORD's fresh data is used up.** Any version 3.3 would need new labelled receipts to be judged, ideally a company's own reviewed ones.

## What would come next (not done)

- **Block only on restricted references that are the same item** (the word test as well as the threshold), not on any restricted line above a low threshold.
- **Make memory recognise only same-word matches,** so "Night cream Soybean" can't match "Cream [L]".
- **Test it on new labelled receipts.** None are left in CORD.
- **Until then, keep the model on every line,** as main does.
