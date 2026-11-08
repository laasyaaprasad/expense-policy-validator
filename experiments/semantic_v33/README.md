# Experiment 11: version 3.3 of the lookup, tested on look-alike traps

**Question:** can one version of the lookup keep the same-item rule's safety without its over-blocking? CORD's fresh receipts are used up, so safety was tested directly, on 66 restricted items written to look like the lookup's food memory ([traps/](traps/README.md)). The plan, the bar and the trap set were committed before any model or embedding saw the traps ([PLAN.md](PLAN.md)).
- **Run it with** `make semantic-v33`.
- **Results:** [results/metrics.json](results/metrics.json), [results/traps.csv](results/traps.csv), [results/stream.csv](results/stream.csv).

**Answer: no. Version 3.3 fails two of its five bars, though narrowly. The traps also showed that the pipeline's current lookup (V3) is clearly unsafe.**
- **V3.3, the safety bar:** with the memory learned from 1,000 receipts, it called **one** restricted trap safe. "Makgeolli Original", a Korean rice wine, was decided as food from the reviewed line "ORIGINAL", a flavour option.
- **V3.3, the usefulness bar:** it decided **33.5%** of replay lines, against a 35% bar.
- **V3, the pipeline's lookup:** it called **three** alcoholic drinks food, which the model alone got right:
  - "Arak Bali" was matched to "Nasi Campur ala Bali";
  - "Hite Jinro Grapefruit" (a soju) to "ORANGE JUICE";
  - "Bintang Radler Lemon" (a beer) to "Lemon".

## Results

**On the traps:** 66 restricted lines and 22 safe ones, with MCC 5812 throughout.

| | M · the model on every line | V3 · the pipeline's lookup | V3.2 · experiment 10 | **V3.3** |
|---|---|---|---|---|
| **Restricted traps called safe**, committed memory | | **2** | 0 | **0** |
| **Restricted traps called safe**, memory after the replay | | **3** | 0 | **1** ("Makgeolli Original") |
| Restricted traps missed by the system, after the replay | 0 | 3 | 0 | 1 |
| Restricted traps sent to a person (EXC-03) | 2 | 2 | 2 | 2 |
| Safe traps decided by the lookup (all right) | | 11 | 2–3 | 3–4 |
| Safe traps wrongly flagged | 0 | 0 | 0 | 0 |

The two traps that went to a person in every arm were "Bantal leher" (a neck pillow) and "Poker chips set". The model was unsure of both (confidence 0.48 and 0.35), which is the review step working as designed.

**On the replay:** CORD training R600–R799, which has been seen before.

| | M | V3 | V3.2 | **V3.3** |
|---|---|---|---|---|
| Lines right (of 635) | 599 | 608 | 599 | 599 |
| False alarms (of 184 clean) · missed violations (of 16) | 9 · 4 | 8 · **5** | 9 · 4 | 9 · 4 |
| **Lines decided by the lookup** | | 66% | 3.6% | **33.5%** |
| Its decisions right, sure labels | | 409 / 410 | 23 / 23 | 212 / 212 |
| Restricted items called safe | | 1 ("Night cream Soybean") | 0 | 0 |
| Receipts needing no model call | | 94 | 4 | 50 |

**The bar:**

| Bar | V3.3 |
|---|---|
| 1. The project's own sets: 28/28, 65/65, dev 43/43, reworded 64/65 and 27/28, the same as M | ✓ |
| 2. No restricted trap called safe, with both memories | ✗ (1, after the replay) |
| 3. Replay: safe, and as accurate as M | ✓ |
| 4. Useful: at least 35% of replay lines | ✗ (33.5%) |
| 5. Repeatable (4.6 × 10⁻⁷) | ✓ |

**Spend:** $0.0085, for the model on the traps.

## What each change did

| | Replay lines decided | Restricted called safe, replay | Traps called safe |
|---|---|---|---|
| V3.3 | 33.5% | 0 | 0 · 1 |
| **Without the second vote** | 53.2% | **1** ("Bintang L", a beer, via "+L") | 0 · 1 |
| **Without the word list** | 33.5% | 0 | 0 · 1 |

- **The second vote (memory and library must agree) is what kept the replay safe.** It also costs the most: it sent 125 lines to the model, nearly all of which memory would have got right.
- **The word list sent 17 traps to the model, but none of them needed it.** The embedding rules alone also called no trap safe with the committed memory. On the replay it caught one line.
- **Blocking at 0.855 again fixed V3.2's over-blocking:** 16 lines were blocked, against V3.2's 331.

## Why "Makgeolli Original" got through

Four checks each missed it:
- **The word test let one shared word count.** {makgeolli, original} against {original} is a Jaccard of 1/2, which meets the 0.5 bar. Experiment 9's amendment closed this hole for containment, but not for Jaccard. "Bintang L" against "+L" got through the same way.
- **Moving the blocking threshold back up let it through.** The nearest restricted reference was "MAKOLI", the same drink spelled differently, but below 0.855. V3.2's lower block (0.665) had stopped it.
- **The library's closest category was food,** so the second vote agreed.
- **No word list holds "makgeolli".**

## What this shows

1. **The pipeline's lookup (V3) is unsafe.** It called two restricted traps safe with the memory it ships with, and three after more learning. It now has four known restricted errors. The branch should not keep it as it is.
2. **Version 3.3 is much safer than V3,** and the model was never worse for it on the replay. But it fails as pre-registered. Nothing is changed after seeing the traps.
3. **A next version would require two or more shared words** for a memory match, not just a Jaccard of 0.5. That would have stopped both "Makgeolli Original" and "Bintang L". But it would be fitted to these traps, so it would need new traps written by someone else, or fresh receipts.
4. **Safety and usefulness pull against each other at every step.** Each rule that stopped a restricted error also sent many correct food lines to the model. At about a third of lines decided, the saving is small next to the risk.

## Data

The traps and their labels are the assistant's, written knowing the design (see [traps/README.md](traps/README.md)). The replay receipts are CORD's, already seen in experiment 10. OpenRouter (Fireworks) received the 88 trap lines. Embeddings are local.
