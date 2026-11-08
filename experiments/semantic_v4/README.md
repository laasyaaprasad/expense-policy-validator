# Experiment 12: every word accounted for, by meaning

**Question:** can a lookup that matches by meaning, but only when every word on both sides is accounted for, catch every trap? And does matching by meaning decide more lines than exact word matching? The plan, the bar and a **blind** trap set were committed before the rule ran on any of them ([PLAN.md](PLAN.md), [traps/](traps/README.md)). A fresh agent that hadn't seen the design wrote the traps.
- **Run it with** `make semantic-v4`.
- **Results:** [results/metrics.json](results/metrics.json), [results/traps.csv](results/traps.csv), [results/stream.csv](results/stream.csv).

**Answer: version 4 is completely safe but fails the bar that asks whether meaning adds anything.** So it does not go into the pipeline.
- **Safe:**
  - **The traps:** across both trap sets (57 and 66 restricted look-alikes), with both memories, the lookup called **no** restricted trap safe.
  - **The replay:** it made no error on a sure label.
- **Meaning added nothing:** V4 decided **250** replay lines. Exact word matching (A), with no embeddings at all, decided **261**, equally safely.
- **The blind traps confirmed that the pipeline's lookup (V3) is unsafe.** On traps it had never seen, it called four restricted items food:
  - "LONG ISLAND ICED TEA", via "THAI ICED TEA";
  - "Kahlua Milk", via "S-Chocolate Milk";
  - "BINTANG PILSENER 620ML", via "Teh Pucuk 350 ml";
  - "Creambath Alpukat", a salon hair treatment, via "Cream [L]".

## Results

**The traps:** restricted traps called safe by the lookup, with the committed memory · with the memory after the replay.

| | Blind set (57 restricted) | Experiment 11's set (66) |
|---|---|---|
| **V3** · the pipeline's lookup | **2 · 4** | **2 · 3** |
| **A** · exact word sets | 0 · 0 | 0 · 0 |
| **V4** · every word, by meaning | **0 · 0** | **0 · 0** |
| V4 + risk words | 0 · 0 | 0 · 0 |

**The model alone (M) on the traps:**
- **On the blind set,** it missed one restricted item: "MARINA HBL SUSU 185ML", a hand-and-body lotion, which it called food. It also wrongly flagged one safe item: "BIR PLETOK BETAWI", a non-alcoholic spiced drink, which it called alcohol.
- **On experiment 11's set,** it missed none, and sent two to a person.
- **The lookup arms made no difference to either.** V4 and A sent all of these lines to the model.

**The replay:** CORD training R600–R799, already seen.

| | M | V3 | A | **V4** | V4 + risk words |
|---|---|---|---|---|---|
| Lines right (of 635) | 599 | 608 | 604 | 605 | 604 |
| False alarms (of 184) · missed violations (of 16) | 9 · 4 | 8 · **5** | 9 · 4 | 9 · 4 | 9 · 4 |
| **Lines decided by the lookup** | | 65.5% | **41.1%** | **39.4%** | 35.4% |
| Its decisions right, sure labels | | 409 / 410 | 260 / 260 | 248 / 248 | 223 / 223 |
| Restricted items called safe | | 1 | 0 | 0 | 0 |
| Receipts needing no model call | | 94 | 58 | 53 | 46 |

**The project's own sets:**
- **V4:** identical to M: 28/28 receipts, 65/65 items, dev 43/43, reworded 64/65 and 27/28. It decided 1 of the 32 receipts' lines; their English lines rarely share words with CORD's memory.
- **A**, checked after the run because the plan didn't include it: also identical to M, deciding 3 lines ([results/own_sets_A_after_the_run.json](results/own_sets_A_after_the_run.json)).

**The bar:**

| Bar | V4 |
|---|---|
| 1. The project's own sets | ✓ |
| 2. No restricted trap called safe, both sets, both memories; no more missed than M | ✓ |
| 3. Replay: safe, and as accurate as M | ✓ |
| 4. Meaning adds something: more replay lines decided than A | ✗ (250 against 261) |
| 5. Repeatable (5.4 × 10⁻⁷) | ✓ |

**Spend:** $0.0096, for the model on the blind traps.

## Why meaning didn't help

**τ_word came out at 0.934, which is almost exact matching.**
- **The floor decided it.** In this embedding model, single words sit very close together:
  - "tablet" against "notebook": 0.924;
  - "cerveza" (beer) against "lemonade": 0.921;
  - "sangria" against "lemonade": 0.920.
- **Why the floor matters:** to make sure no drink word can stand in for another, the threshold had to clear those pairs.
- **The calibration agreed:** 2,398 of the 2,399 pairs above the floor agreed on category. The one that didn't was "BCA PROMO", the known mislabel.

**What meaning gained: 6 lines,** all typos or size words:
- "Soaghetti Deluxe" ≈ "Spaghetti Deluxe";
- "MAC N CHEEESE" ≈ "MAC N CHEESE";
- "=\*LARGE\*==" ≈ "Grande";
- "CHOCO" ≈ "CHOCOLATE".

**What it lost: 17 exact repeats.** These include "Mineral Water", "HOT TEA", "ORANGE JUICE" and "Ice Lemon Tea". The sentence-level restricted check (similarity of at least 0.855 to any restricted line) blocked them, because ordinary drinks sit close to wines and cocktails in this model. A has no such check, because exact words can't be fooled by resemblance.

## What this shows

1. **Word-by-word matching closes the trap hole.** It matched experiment 11's traps, which it was designed after, and the blind set, which it wasn't. No restricted item got through, in any arm except V3.
2. **With this embedding model, meaning at the word level only fixes typos.** The threshold that keeps drinks apart also keeps nearly everything else apart.
3. **Exact word sets (A) were the safest *useful* arm:**
   - no embeddings, no torch, no 4 GB Lambda;
   - 41% of replay lines decided, all right;
   - zero traps let through;
   - identical to the model on the project's own sets.
   
   It wasn't the pre-registered candidate. Adopting it is a decision for the user, made after seeing this run.
4. **V3 should leave the pipeline.** It let four of the blind traps through, on top of the four restricted errors already known from experiments 10 and 11.
5. **The model isn't perfect either.** It called a body lotion food and a non-alcoholic drink alcohol. Human review of the model's unsure lines is still the backstop.

## Data

The blind traps were written by a model that didn't see the design, and labelled by the assistant (see [traps/README.md](traps/README.md)). The replay receipts are CORD's, already seen. OpenRouter (Fireworks) received the 79 blind trap lines. Embeddings are local.
