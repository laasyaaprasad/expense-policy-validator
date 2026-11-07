# Experiment 7: a local semantic lookup first, the model only when it isn't sure

**Question:** can a semi-deterministic classifier replace main's? The proposed classifier:
- runs a local, repeatable semantic lookup that decides the lines whose meaning is clear and safe;
- sends everything else to the model at temperature 0;
- sends the model's unsure lines to a human.

The plan and the bar were committed before any embedding or model call ([PLAN.md](PLAN.md)). So were CORD's held-out test split and its labels ([cord/test](../../cord/test/README.md)).
- **Run it with** `make semantic-v2`.
- **Results:** [results/metrics.json](results/metrics.json), and every line with who decided it in [results/lines.csv](results/lines.csv).

**Answer: not yet.**
- **As built, the lookup is safe and perfectly repeatable, but too cautious to be useful.** It decided 6% of real receipt lines against the 20% bar, so it fails one bar. Every result equals main's, because the lookup only took lines the model also got right.
- **Letting it learn from reviewed receipts made it useful at once:** it took 67% of the fresh receipts' lines, and 45 of 100 of those receipts needed no model call. **But it then missed a real violation:** it decided "MAKOLI" (makgeolli, Korean rice wine) was food. That is the central risk of a learning lookup.

## Results

| | M · main (model, temperature 0) | V2 · lookup first | V2+L · after learning |
|---|---|---|---|
| 28 receipts exactly right · items | 28/28 · 65/65 | 28/28 · 65/65 | |
| False positives · missed violations | 0/9 · 0/16 | 0/9 · 0/16 | |
| Timing receipts · dev set (traps) | 4/4 · 43/43 | 4/4 · 43/43 | |
| Reworded: items · receipts | 64/65 · 27/28 | 64/65 · 27/28 | |
| CORD validation: lines · false alarms · missed | 252/270 · 12/98 · 0/2 | 252/270 · 12/98 · 0/2 | |
| **CORD test** (fresh): lines · false alarms · missed | 269/287 · 13/96 · 0/4 | 269/287 · 13/96 · 0/4 | 269/287 · 12/96 · **1/4** |
| **Lines the lookup decided on real receipts** | | **34 of 557 (6%)** | **193 of 287 (67%)** on CORD test |
| Its decisions on sure labels: right · restricted errors | | 58/58 · 0 | 188/189 · **1** |
| Receipts needing no model call | | 0 of 32; 3 and 2 of 100 on CORD | **45 of 100** on CORD test |
| **Verdict by the bar** | | **fails bar 7** (useful) | not part of the decision |

**The other bars:**
- **Repeatability is exact:** all 1,229 texts embedded again in a fresh process gave identical vectors (largest difference 0.0).
- **Cost:** $0.030 in model calls, for main on the 100 fresh receipts; $0 for embeddings.

## Why the lookup decided so little

Of its 750 lines, 413 went to the model on the **restricted guard** and 191 on the **margin**:
- **Restricted guard:** the 10 nearest library lines must all be risky, or all safe.
- **Margin:** below the cutoff the library chose for itself, 0.108.

The small local model (0.6B) puts meanings less far apart than experiment 6's hosted 8B model. A food line's 10 nearest neighbours almost always include a drink, a gift or a household line, so the guard fires. Experiment 6's 8B model decided 30%. **This is the real trade-off:** a model small enough to run locally and repeat exactly is also less discriminating, so the same safety rule lets far less through.

The code checks did what they were added for:
- **"No real words":** "25%", "100%" and "+M" went to the model (12 lines in all).
- **Merchant:** the supermarket, pharmacy and package-store lines went to the model (27).

## The trap items

All 43 dev-set traps are right, under both M and V2. The lookup decided only two dev lines on its own (Margherita pizza, seat upgrade fee), both right. Every misleading name went to the model:
- **by the margin:** Tiramisu, beer cheese pretzel;
- **by the restricted guard:** Shirley Temple, coq au vin, the earbuds;
- **by the code checks:** the cooking wine (a supermarket), the prescription, the smartphone, every guest and gift line.

On the 32 receipts it decided "Beer battered cod & chips" and "Rum cake slice" as food, which is right. Those are the taxonomy's own traps, and the hard negatives were written from the taxonomy's rules, so they are not clean evidence (PLAN.md).

## Learning from reviewed receipts (V2+L)

**What was added:** CORD validation's sure labels, standing in for receipts a person has reviewed: 221 lines, giving a library of 591. The cutoff fell to 0.047. On the 100 fresh receipts the lookup then decided 193 of 287 lines (67%), and **45 receipts needed no model call or translation.** That is the at-scale case for this design: the same cafés and dishes come back.

**And the risk, in one line:** "MAKOLI" (makgeolli, rice wine) on a Korean-restaurant receipt was decided as `food_beverage`, so the violation was missed (1 of 4).
- **Before learning,** its nearest library lines were a mix of tips and a gift card, so the guard sent it to the model, which called it alcohol at 0.999.
- **After learning,** the library was full of Indonesian and Korean dishes, so its 10 nearest were all food, the guard saw no doubt, and the lookup took it.

A library that grows mostly from safe, everyday lines makes the restricted guard blind to the rare risky one. The fix has to be structural:
- never let the lookup decide a line unless a risky library line of the same kind was reachable;
- or keep restricted decisions with the model;
- and audit any learned library before it goes live.

## What this shows

1. **Safe and repeatable is achievable:** zero errors on sure labels, identical vectors on a rerun, no data leaving the machine, $0 per line.
2. **Useful, from a hand-written library and a small local model, isn't, yet:** 6% of lines, and almost no receipt avoids the model call.
3. **Learning is what makes it useful, and also what makes it unsafe** unless restricted decisions are fenced off. The one error was a missed violation, the costlier kind.
4. **Main is unchanged by any of this.** V2 matched main exactly on every set, so there is no accuracy gain to set against the extra parts.

## What would come next (not done: each needs data this run didn't use)

1. **Fence off restricted categories:** the lookup may only ever decide unrestricted ones, and anything near a restricted one goes to the model. That would have stopped the MAKOLI error, and costs little coverage, since nearly all real lines are food.
2. **Validate learned examples before they go in:** a learned line goes in only if the model, run on it on its own, agrees with its label. Run that way, MAKOLI would have been flagged.
3. **Try a stronger local model** (Qwen3-Embedding-4B, about 8 GB) to see how coverage grows with size.
4. **Test on more fresh data.** CORD's 800-receipt training split is the obvious source.

## Data and spend

- **Embeddings:** local, Qwen3-Embedding-0.6B at revision `97b0c614`, in float32. Nothing was sent out. The vectors are cached in `cache/vectors.npz`, so the experiment replays without the model.
- **Model calls:** OpenRouter (Fireworks) received the CORD test lines, as main's classifier sends them. $0.0296 ([spend.jsonl](spend.jsonl)).
- **Library:** 413 lines written; 43 dropped as near-copies of test lines. Not yet checked by hand.
