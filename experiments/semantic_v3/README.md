# Experiment 8: recognise first, generalise carefully, the model as fallback, at scale

**Question:** as receipts arrive and are reviewed, does the semi-deterministic classifier take a growing share of lines from the model, without ever calling a restricted item safe? The plan and the bar were committed before any run ([PLAN.md](PLAN.md)), as were the 400 fresh receipts and their labels ([cord/train](../../cord/train/README.md)).
- **Run it with** `make semantic-v3`.
- **Results:** [results/metrics.json](results/metrics.json); every stream line in [results/stream.csv](results/stream.csv).

**Answer: almost all of it.**
- **Every safety and accuracy bar passes.**
- **No restricted item was called safe.** The makgeolli case now goes to the model, as unfamiliar.
- **The lookup's decisions were 99.6% right,** and the system got **more** stream lines right than main (1,123 against 1,115), with the same false alarms and missed violations.
- **The lookup grew from 14% of lines in the first 100 receipts to 56% and 54% in the last two blocks.** By then a third or more of receipts needed no model call at all.
- **It fails one bar as written:** "the share never falls", by 1.6 points in the last block. The last-block share (54%) clears its own 40% bar.

## The stream: 400 fresh receipts, starting cold, learning after every 50

| Receipts | Lines | Decided by the lookup | From memory · curated | Receipts with no model call |
|---|---|---|---|---|
| 1–100 | 284 | **14%** (41) | 37 · 4 | 14 of 100 |
| 101–200 | 269 | **42%** (112) | 112 · 0 | 32 of 100 |
| 201–300 | 331 | **56%** (185) | 180 · 5 | 41 of 100 |
| 301–400 | 328 | **54%** (178) | 178 · 0 | 31 of 99 |

**Learning:**
- **813 reviewed lines entered memory** under the two-key rule.
- **14 restricted lines became risk references.**
- **43 lines were kept out because the model disagreed with the review, or wasn't sure.**
- **113 were kept out for unsure labels.**

| | M · main (the model on every line) | V3 · lookup first |
|---|---|---|
| Lines right (of 1,212) | 1,115 | **1,123** |
| Lines right, sure labels (of 1,099) | 1,067 | **1,074** |
| False alarms (of 388 clean receipts) | 46 | 46 |
| Missed violations (of 12) | 1 | 1 |
| **Restricted items called safe by the lookup** | | **0** (all 20 restricted lines went to the model) |
| Lookup decisions right, sure labels | | **509 / 511** |
| Lookup decisions the model disagreed with (the spot check) | | 11 of 516 (2.1%) |

**Where V3 beat M (9 lines):** size and option lines ("=\*MEDIUM\*=", "Less Ice", "YELLOW") that the model called `other`. The memory had learned from reviewed receipts that options take their item's category (labelling guide, rule 2). That's a convention learned from the reviewers, not a deeper understanding.

**The lookup's three disagreements with the labels:**
- **"-TIKET", a real miss.** A cinema ticket was decided `airfare` by the curated library. The ticket gate knows "ticket", "billet" and "ingresso", but not the Indonesian "tiket". **Fix for the next version:** add it to the gate, and keep event and transport words apart.
- **"TAKE AWAY CHARGE" (R256), a labelling error.** The lookup said `service_fee`, which is right by the labelling guide (rule 4). The label wrongly says food: the line was missed while labelling. It is reported here and not corrected, because the results had already been seen. Corrected, the lookup is 510/511.
- **"MIKA KECIL" (a small plastic tub), with an unsure label:** the lookup said food, the label says packaging (`other`). It isn't counted.

**Main's own misses are unchanged by V3.** The one missed violation and the 46 false alarms are the model's: packaging read as household goods, as on the other CORD splits.

## How every restricted line was kept from the lookup

All 20 restricted lines in the stream went to the model, which got all but four of their categories right:
- **Unfamiliar** (no curated line close enough): "MAKOLI" (0.79), Bintang beer, a Côtes du Rhône wine, shisha, an iPhone charger, a baby bathtub and romper.
- **The lookup's best guess was itself restricted:** baby oil, creams, cotton balls, a feeding bottle, a white wine.
- **Its guess was a category only the model fills:** breast pads (`health_otc`).

Experiment 7's error can't recur: learned lines only recognise near-repeats (similarity at least 0.855, calibrated on other receipts). "MAKOLI" has no near-repeat in memory, and the curated library finds it unfamiliar.

## The other bars

- **The project's own sets, curated library only:** identical to main.
  - 28/28 receipts, 65/65 items, 0/9 false positives, 0/16 missed.
  - Timing receipts 4/4; the dev set 43/43; the reworded receipts 64/65 and 27/28.
  - The lookup decided 24 of the 32 receipts' 78 lines, 3 of 43 dev traps and 11 of 64 reworded lines.
- **Repeatable:** re-embedding all 1,795 texts in a fresh process changed no vector by more than 6.3 × 10⁻⁷, well within the 10⁻⁵ bar. No decision changed.
- **Calibrated on CORD validation and test only:**
  - near-repeat similarity τ_mem = 0.855: all 1,776 sure-labelled pairs above it agree;
  - familiarity τ_fam = 0.84;
  - risk gap δ = 0.10;
  - curated margin 0.108.

## What this means at scale

- **The mechanism works.** Starting cold, once a few hundred receipts were reviewed, the lookup decided over half the lines, and a third or more of receipts needed no model call or translation. That's with zero restricted errors and accuracy no worse than the model's.
- **Most of the gain is memory, not generalisation:** 507 of 516 decisions were near-repeats of reviewed lines. The curated library on its own decides little on real receipts. That's by design: generalising is where the risk is.
- **The share levels off:** 56% → 54%. New dishes keep arriving, so a large share always needs the model. On a company's mixed receipts the plateau would sit somewhere else, probably lower for one-off purchases and higher for repeat merchants.
- **What it saves:** at this volume, AI calls and translations on a third or more of receipts, faster answers on those, and identical, explainable decisions. In money that's modest at today's prices, about a third of the model bill.

## What this can't show

- **CORD repeats more than company expenses would:** cafés and bakeries, one synthetic context. The merchant check never fired.
- **The labels stand in for human review, and they are the assistant's:** one was found wrong (above), and none were checked by hand.
- **One ordering of one sample of 400 receipts.**
- **The "never falls" bar** was meant to show growth doesn't reverse. A 1.6-point dip on a different mix of receipts doesn't show that it does, but the bar was written that way, so V3 fails it as written.

## Data and spend

- **Model calls: $0.124** ([spend.jsonl](spend.jsonl)). Main on the 400 receipts sent their line text to OpenRouter (Fireworks), as main always does.
- **Embeddings:** local; no data left the machine.
