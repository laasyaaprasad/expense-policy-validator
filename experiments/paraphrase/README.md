# Experiment 5: the scored receipts reworded

**Question:** the classification prompt was written by an assistant that had the 32 receipts and their answer key to hand. Is 65 / 65 partly the prompt recognising these exact item names? Every itemized line on the 28 scored receipts was reworded (64 lines, in their own languages: other words, other brands, and on RCP-004 a different item with the same kind of misleading name). Then the frozen classifier and the unchanged engine were run on them. The plan and the bar for the result were committed before any call ([PLAN.md](PLAN.md)), and every variant was checked by hand first ([review.csv](review.csv)). Run it with `make paraphrase`: offline, from `cache/`. Results: [results/metrics.json](results/metrics.json), per line in [results/lines.csv](results/lines.csv).

**Answer: no sign of overfitting to the wording, by the bar set in advance, but the one miss is the trap the brief names.**

| | Original wording | Reworded |
|---|---|---|
| Items right | 65 / 65 | 64 / 65 |
| Macro F1 · restricted F1 | 1.000 · 1.000 | 0.995 · 0.976 |
| Receipts exactly right | 28 / 28 | 27 / 28 |
| False positives · missed violations | 0 / 9 · 0 / 16 | 0 / 9 · 0 / 16 |
| Lines below the 0.5 threshold | 0 | 0 |

The bar was at least 62 / 65 items and 26 / 28 receipts. All four "same trap" lines on RCP-004 were right: ale-battered haddock, Virgin Mary, piña colada sin alcohol and bourbon pecan pie were all food, not alcohol. So were the wines named only by region or grape (Montepulciano d'Abruzzo, Ribera del Duero, Brunello, Riesling), the spa charge, the tablet in Japanese and the named recipients. 36 calls in all, including the three repeats below: $0.0059.

## The miss: cooking wine

"Cooking wine - Marsala 375ml" became "Shaoxing cooking wine 640ml" (RCP-014-L4). The classifier called it `food_beverage` at 0.88, "cooking wine used as a food ingredient", so ALC-03 (alcohol from a store that isn't a restaurant, bar or hotel) was missed. The receipt was still flagged, by HOUSE-01 and DOC-03, so its disposition stayed right. On a receipt with only the cooking wine, it would have passed: a missed violation.

The brief names this case: "cooking wine" from a supermarket really is alcohol. The prompt already says so too, since its alcohol definition includes "wine or spirits sold as a product for cooking" (`validator/classifier.py`, `DEFINITIONS`). The model went against it. The original Marsala line was already the system's closest call (0.900, the lowest of the 65; `docs/evaluation.md`, section 6). So on the one item where the policy cuts against intuition, the answer depends on the wording. A wine the model knows as a drink (Marsala) stays alcohol; a cooking wine it knows as a seasoning (Shaoxing) does not, even with the rule in front of it.

**After the run, not in the plan:** RCP-014 was classified three more times, as fresh calls (`repeats_of_misses` in the metrics). The line came back `food_beverage` each time, at 0.68, 0.18 and 0.78. So the miss is stable, and the confidence isn't: one sample in four would have fallen below the threshold and sent the receipt to review, and none would have raised ALC-03.

**What I would change, and haven't:** the rule is in the definition but not among the prompt's misleading-name rules, which is where the model looks for exceptions. A line there ("cooking wine, of any kind, is alcohol") would put it next to the opposite case (a dish cooked with a spirit is food). The dev set could also gain cooking wines other than Marsala (mirin, Shaoxing, with sherry vinegar as a contrast). That would be tested on the dev set first. It wasn't made, since nothing is tuned on what this run shows.

## What this can't show

- It tests the wording, not the receipts: the amounts, merchants and situations are the same 28, so it says nothing new about false-positive rates.
- The variants were drafted by the assistant that wrote the prompt, knowing the categories, then checked by hand. A blind set written by someone else is still the stronger test.
- One sample per line, at temperature 1, except the repeated RCP-014.
