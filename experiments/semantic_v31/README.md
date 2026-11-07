# Experiment 9: a stricter "same item" rule for the lookup's memory

**Question:** the pipeline's lookup (version 3) remembers reviewed lines and decides any line within similarity 0.855 of one. Wiring it in showed that this admits similar items, not only repeats, e.g. "Grilled salmon entree" decided from "Chicken Steak". Does a stricter rule keep the safety and accuracy, deciding only true repeats, and what does it cost? The rule: close in meaning **and** sharing the words. The plan and the bar were committed before any call ([PLAN.md](PLAN.md)), with one amendment before any run: a single shared word isn't enough. So were the 200 fresh receipts and their labels ([cord/train2](../../cord/train2/README.md)).
- **Run it with** `make semantic-v31`.
- **Results:** [results/metrics.json](results/metrics.json); every line, for both arms, in [results/stream.csv](results/stream.csv).

**Answer: the stricter rule is safe but fails its usefulness bar,** deciding 21% of fresh lines against the 35% bar, so the pipeline keeps version 3. The reason is a single mislabelled line:
- The calibration needed **every** same-item pair to agree on category.
- The one pair that didn't was "TAKE AWAY CHARGE", labelled `service_fee` on one receipt and `food_beverage` on another. The second label is the labelling error experiment 8 reported (R256).
- That one error forced the threshold to 1.0: exact text only.

**What it shows about version 3:**
- **It's looser than its name says:** 138 of its 336 memory decisions (41%) matched a reviewed line that fails the word test.
- **It still made no error on a sure label.**
- **It was the most accurate arm:** 515 lines right against the model's 505.

## Results on the 200 fresh receipts (CORD training R400–R599, 569 lines)

The stream starts from the pipeline's memory (813 lines learned from R000–R399) and learns after every 50 receipts.

| | M · the model on every line | V3 · the pipeline's lookup | V3.1 · same-item rule |
|---|---|---|---|
| Lines right | 505 | **515** | 505 |
| Sure lines right (of 479) | 467 | **476** | 467 |
| False alarms (of 192 clean) · missed violations (of 8) | 23 · 2 | 22 · 2 | 23 · 2 |
| **Lines decided by the lookup** | | **336 (59%)**, all from memory | 122 (21%): 110 from memory, 12 from the library |
| Its decisions right, sure labels | | 326 / 326 | 122 / 122 |
| **Restricted items called safe** | | **0** | **0** |
| Memory matches failing the word test | | 138 of 336 | 0 |
| Receipts needing no model call | | **84** | 29 |

**On the project's own sets, V3.1 with the committed memory:** 28/28 receipts, 65/65 items, dev set 43/43, reworded 64/65 and 27/28, the same as M. It decided 24 of the 32 receipts' 78 itemized lines.

**Repeatable:** re-embedding every fresh line in a fresh process changed no vector by more than 5.8 × 10⁻⁷.

**Spend:** $0.060, for the model on the 200 fresh receipts.

## What V3's looseness looks like

V3's six errors were all on unsure labels. Every one matched by meaning, not by item:
- "MIKA BESAR" and "MIKA SEDANG" (plastic tubs) were decided as food from "- Sedang", a size option;
- "OPEN KITCHEN" (a kitchen note) was decided as food from "BBQ".

They're harmless here, since packaging and notes are `other`, which no rule acts on. But they are the same mechanism that could, in principle, carry a restricted item into a safe category. On these 200 receipts it didn't: all 12 restricted lines went to the model. Six of those were beers, together with makgeolli, the iPhone charger, a face mask, rice soap and shoes.

## What this means

1. **Calibrating to 100% agreement is fragile to label noise.** One wrong label among 2,752 same-item pairs decided the threshold. A real deployment calibrates on human reviews, which also contain mistakes, so the rule needs a tolerance, or a cleaned calibration set.
2. **The stricter rule does what it was meant to:** it never decides by mere resemblance (0 of its matches fail the word test). Its cost here comes from the forced 1.0 threshold, not from the word test itself.
3. **V3 stays in the pipeline, with its looseness documented.** It is the most accurate arm on fresh receipts and has made no error on a sure label across 600 fresh receipts (experiments 8 and 9). But nearly half its memory decisions are resemblance, not repetition.

## What would come next (needs data this run didn't use: CORD training R600–R799 remain)

- **Correct the R256 label.** It is objectively wrong by the labelling guide, and was reported before this experiment ran. Then recalibrate τ_same on the corrected data, with a small tolerance (e.g. 99.9%) so one bad label can't decide it.
- **Test that on the last 200 training receipts,** against V3, with the same bar.

## Data

The labels are the assistant's and not checked by hand. One is known to be wrong (above). OpenRouter (Fireworks) received the 200 receipts' line text. Embeddings are local.
