# Experiment 6: a semantic first stage, then the model at temperature 0

**Question:** can a semi-deterministic classifier replace the current one? A semantic stage decides the lines whose meaning is clear and safe from labelled example lines (anchors), and every other line goes to the model at temperature 0. The design and the bar were committed before any call ([PLAN.md](PLAN.md)), including one amendment made before any result. Run it with `make semantic-first`. Results: [results/metrics.json](results/metrics.json); per line, for every test set, [results/lines.csv](results/lines.csv).

**Answer: the model at temperature 0 (arm B) passes every bar and does as well as or better than the submitted classifier everywhere. The semantic stage (arm C) fails one bar as written: it made two restricted-flag errors, both on CORD lines whose labels are themselves marked unsure, and the model alone made the same call on both.** It decided 30% of lines (the bar was 25%), with every one of its 72 decisions on the project's own receipts, dev set and reworded lines right. But it saves almost no model calls.

## Results

| | A · submitted (temperature 1) | B · model only, temperature 0 | C · semantic stage, then B |
|---|---|---|---|
| 28 receipts exactly right · items | 28/28 · 65/65 | 28/28 · 65/65 | 28/28 · 65/65 |
| False positives · missed violations | 0/9 · 0/16 | 0/9 · 0/16 | 0/9 · 0/16 |
| Timing receipts | 4/4 | 4/4 | 4/4 |
| Dev set (43 trap items): category · kind · recipient | 42 · 43 · 43 | **43** · 43 · 43 | **43** · 43 · 43 |
| Reworded (experiment 5): items · receipts | 64/65 · 27/28 | 64/65 · 27/28 | **65/65 · 28/28** |
| CORD: lines right · false alarms · violations caught | 250/270 · 13/98 · 2/2 | **252**/270 · **12**/98 · 2/2 | **252**/270 · **12**/98 · 2/2 |
| Fresh repeat: categories changed | 0 of 79 (on the 32) | 0 of 121 | 0 of 121 |
| Without merchant and expense type | 65/65 | 65/65 | n/a |
| **Verdict by the bar** | | **passes** | **fails bar 7** (restricted errors) |

**The semantic stage on its own:**
- **Embedding model:** qwen3-embedding-8b, chosen on the anchors alone. Its nearest-anchor accuracy was 0.93, against 0.74 for multilingual-e5-large and 0.73 for bge-m3.
- **Cutoff:** 0.123, chosen on the anchors leave-one-out: 155 decided, 154 right.
- **Lines decided:** 151 of 455.

| Set | Decided | Right |
|---|---|---|
| 32 receipts | 29 of 78 | 29 |
| Dev set | 17 of 43 | 17 |
| Reworded | 26 of 64 | 26 |
| CORD | 79 of 270 | 76 |

- **Why the other 304 lines went to the model:**
  - the restricted guard: 197;
  - a margin below the cutoff: 73;
  - a text gate (a possible recipient, phone, prescription or ticket): 20;
  - a category only the model can fill (other, health_otc, gift): 14.
- **Receipts needing no model call at all:** 3 of 32, 2 of 14 (dev), 4 of 28 (reworded), 8 of 100 (CORD). Nearly every receipt has at least one line that needs the model, so the call is still made.

## The trap items (dev set)

All 43 dev items are right under B and C. What decided each trap:

| Trap | Who decided | Result |
|---|---|---|
| Margherita pizza, Tiramisu (made with liqueur), Coq au vin (cooked with wine) | semantic stage | food, right. Their nearest anchors were all food, so the guard didn't fire |
| Beer cheese pretzel | model (the guard: beer and food anchors both near) | food, right |
| Shirley Temple | model (the stage's best guess was `other`) | food, right |
| Vino blanco para cocinar (cooking wine) | semantic stage | alcohol, right |
| Smartwatch strap, Seat upgrade fee, Express pressing | semantic stage | right |
| Kabellose Kopfhörer, Resort fee | model (guard, cutoff) | right |
| Prescription (receita médica), smartphone, a lunch or gift naming a guest | model (text gates) | right, with item kind and recipient |
| **Whiskey stones gift set** | model (gift gate) | **household goods, right.** The submitted classifier got this wrong (gift, 0.43); at temperature 0 the model gets it right, but at only 0.61–0.81 |

On the reworded receipts, the semantic stage decided "Shaoxing cooking wine 640ml" as alcohol. That's right, and it's the one line the model gets wrong (food, every time). So C has 65/65 there where B has 64/65.

## Why C fails bar 7: its three wrong lines, all on CORD

| Line | Semantic stage | Label | Label sure? | Model alone (B) |
|---|---|---|---|---|
| `*Rhum` | alcohol | food (rum flavouring for a bakery item) | no | alcohol, 0.98 |
| `Free Mini Candle` | household goods | other | no ("arguably household_goods") | household goods, 0.89 |
| `25%` | service fee | food (most likely a sugar level on a drink) | yes | other, 0.84 |

- **The two restricted-flag errors are disputed labels.** In both, the model alone gave the same answer, so C's CORD numbers equal B's. On lines with a sure label, the stage's only error is `25%`. That error is a real weakness: a bare number has no meaning without its receipt, and the stage never sees the receipt.
- **The bar said no restricted-flag error, and these are two.** So C fails as written. Moving the bar now, after seeing which lines failed, would be exactly the tuning the plan rules out.

## What this shows

1. **Temperature 0 works on the pinned host, and is the change to make.** The logprobs at temperature 0 are informative. B passes every bar and is better than the submitted classifier on the dev set (43 vs 42, fixing the whiskey stones), on CORD (252 vs 250 lines, 12 vs 13 false alarms), and equal everywhere else. The brief asks for temperature 0 "where you can", and now it can.
2. **Temperature 0 isn't bit-identical.** All 248 calls were served by Fireworks, and no category changed on a fresh repeat. But confidences on borderline lines moved: the whiskey stones line went 0.61 → 0.81, and the Marsala cooking wine 0.90 → 0.99. Across runs it isn't even category-stable on borderline lines: a second full run from scratch, on a fresh clone, changed 6 of the 455 lines under B, every one below 0.84 confidence in one of the two runs. They were the Shaoxing wine (food 0.83, then alcohol 0.56) and five CORD lines (giving 255 lines and 9 false alarms, against 252 and 12). None of the 79 lines on the 32 receipts changed, and both verdicts held. The embedding API varied slightly too, changing two semantic-stage decisions. The cache is still what makes reruns identical; the committed results are the first run.
3. **The semantic stage is name-driven.** That's why it gets "Shaoxing cooking wine" right where the model doesn't. It's also why it calls `*Rhum` alcohol and `25%` a service charge.
4. **Its guard only protects where the anchors show the trap.** Its safety depends on alcohol and food anchors both sitting near a misleading line. No trap examples were written, on purpose, to avoid copying the test sets. So the guard misses traps whose name looks purely like one category (`*Rhum`, Tiramisu). It got Tiramisu right by luck of the neighbours.
5. **Semi-deterministic, but not cheaper.** The stage decides 30% of lines, every one reproducible and explained by its nearest anchors. But only 3 of the 32 receipts avoid the model call, so cost and latency barely change. Its value is auditability, not money.
6. **Decontamination mattered.** 39 of the 391 anchors were near-copies of test lines ("Leather belt", "Wool scarf", "Ribeye steak", "Greeting card"). The assistant writing them had seen the test sets. Without this step the stage would have looked better than it is.

## What would come next (not done: each needs a new version, tested on data this run didn't use)

- **Adopt B:** the classifier at temperature 0, everything else unchanged. It passed the bar set in advance.
- **A second version of the semantic stage, tested on CORD's unused test split:**
  - a gate for lines with no words, such as `25%`;
  - a minimum line length;
  - hand-written hard negatives (dishes made with alcohol, flavourings). Writing those needs care, since they're the traps the test sets contain.
- **The semantic stage as a second opinion rather than a replacement:** run it beside the model, and send any disagreement to review. On this run that would have sent the Shaoxing receipt to review instead of missing ALC-03, without overriding the model anywhere.

## Fixes found while building it

- **The plan's confidence rule had two flaws,** found by the unit tests before any result: it fell slightly as the margin rose, and a line clearer than every anchor got exactly 0.5. It was amended before the rerun (PLAN.md, "Amendment, before any result"). No decision depended on it.
- **The first run stopped while caching embeddings:** qwen3's vectors are longer than the others'. Each model now has its own cache file.

## Spend and data

- **$0.0546 in all** ([spend.jsonl](spend.jsonl)): 248 classification calls and 117 translations, all fresh, plus the embeddings.
- **Who received what:**
  - **DeepInfra** (embeddings) received the anchors and the line text of every test set.
  - **Fireworks** (classification) and the translation host received what the submitted system sends.
  - No amounts, employee fields or labels were sent.
- **The cache isn't committed yet (24 MB),** mostly classification responses with five alternatives per token. Without it, a rerun needs a key.
