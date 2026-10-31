# Evaluation (Part 3)

Every number here comes from `make eval`, which scores the committed outputs on the 28 in-scope receipts and writes [`outputs/v1/metrics.json`](../outputs/v1/metrics.json); the viewer's Metrics page shows the same file. Intervals are 95% Wilson intervals. Matching follows the data dictionary. An exception matches on receipt + `rule_id`, and its evidence is correct when the `line_ids` are exactly equal. A signal matches on receipt + `rule_id`.

**v1 is blind and final.** The prompt, threshold, rules and model were committed and tagged `v1-frozen` before any real receipt was classified, and that first run was committed before it was scored. Nothing was changed after seeing a gold error, so the final numbers are the v1 numbers. One caveat: the engine was aligned with the answer key in oracle mode *before* the freeze, so v1 is blind for translation, classification and the threshold, but not for the engine (README, "Oracle mode").

## 1. Classification (65 items)

| | System | Keyword baseline |
|---|---|---|
| Macro F1, over the 19 categories in the answers | **1.000** | 0.738 |
| Restricted F1 (restricted = positive) | **1.000** | 0.791 |
| Items right | 65 / 65 (CI 94–100%) | 49 / 65 |

**What each score tells us about the engine's inputs:**
- **Restricted F1** measures the input that matters most. A missed restricted item becomes a missed violation, and a false one becomes a false flag. At 1.000, the engine gets every restricted item and no false ones.
- **Macro F1** also covers the unrestricted categories, which drive caps and scope: food, drink and service fees make up meal spend for MEAL-01/02, and `computer_accessory` scopes TECH-02. At 1.000 those are right too. With 7 one-item categories, one error would move macro F1 by about 5 points, so it is a noisy number. Per-category counts are in `metrics.json`.
- **Isolating the classifier.** Oracle mode (the engine on gold categories) also scores 28/28, so on this data the classifier is the only stage that could have hurt, and it didn't.
- **Stability.** A fresh repeat of all 31 calls at temperature 1 changed 0 of 79 answers. A run without the merchant and expense type still got 65/65.

**Why the score is this clean:**
- The traps in the data are the examples the taxonomy and meals policy themselves give: beer-battered cod, Arnold Palmer, virgin mojito, cooking wine, rum cake.
- The prompt quotes none of them, but a capable model given those documents is expected to get them right.
- The prompt and dev set were written by people who had read the receipt lines and the aggregate label counts while planning. So 65/65 is not a forecast for new receipts.

## 2. Disposition

Rows are the correct answer, columns are ours.

| | flag | review | pass |
|---|---|---|---|
| **correct flag** (16) | 16 | 0 | 0 |
| **correct review** (3) | 0 | 3 | 0 |
| **correct pass** (9) | 0 | 0 | 9 |

28 / 28 receipts are exactly right (CI 88–100%): the disposition, every exception with exactly its line IDs, and the signals. All 26 gold exceptions are found, none is extra, and all 26 cite exactly the gold lines.

## 3. False positives

**0 of 9** receipts that should pass were flagged or sent to review (CI **0–30%**). Several are built to look suspicious, and all of them pass: the trap menu on RCP-004, the client dinner with a bottle of Chianti (RCP-002), and the dry-cleaning bill (RCP-009). But nine receipts cannot rule out a false-positive rate as high as 30%.

## 4. Missed violations

**0 of 16** receipts that should be flagged were passed or downgraded to review (CI **0–19%**).

## 5. Keyword baseline

`rules/keywords.yaml` holds restricted terms in all six languages and unrestricted ones in English, with no brand names. It was committed before its first run. Its labels go through the same retriever, engine and reporter, so the comparison isolates the classifier.

| | System | Baseline |
|---|---|---|
| Receipts exactly right | 28 / 28 | 20 / 28 |
| False positives | 0 / 9 | 2 / 9 (CI 6–55%) |
| Missed violations | 0 / 16 | 2 / 16 (CI 3–36%) |
| Exceptions found / raised / exact line IDs | 26 / 26 / 26 | 21 / 24 / 20 |

The baseline's 16 item errors are the ones a word list must make:
- **Misleading names.** Beer-battered cod, rum cake and the alcohol-free mojito read as alcohol, so RCP-004, which should pass, is flagged.
- **Brand names.** Chianti, Barolo, Marlboro and the iPad are missed. RCP-024 drops from flag to review.
- **No translation.** The French and German dishes become `other`.
- **Laundry read as clothing.** "Dry cleaning - 3 shirts" and "Limpeza a seco - 2 camisas" become clothing, so a dry-cleaning bill that should pass is flagged.
- **No recipients.** The gift basket for a city procurement official is judged under GIFT-01's cap instead of GIFT-04, so RCP-022 passes.

**Where the baseline does better:** it costs nothing, runs in milliseconds, gives the same answer with no cache, and sends no receipt data outside. It is not more accurate on any measure here.

## 6. Two failure cases

The system gets none of the 28 in-scope receipts wrong, so the blame report (`outputs/v1/blame.json`) is empty. Its tests plant one fault per stage to show it names the right one. The receipts it does get wrong are the four timing receipts, which are out of scope on this track. The blame report runs on them too, and names **rule logic**: oracle mode gets them wrong as well.

- **RCP-020 / RCP-021**: one employee's $74.00 lunch and $71.50 dinner at the same restaurant on the same day. Each is under the $75 meal cap; together they are over it. We raise MEAL-01 on both. The answer is TIME-03 (split transactions), which replaces the cap it splits around. *Change:* build TIME-03 on the per-employee-day meal totals the engine already computes, grouped by merchant name. When every receipt in the group is at or below the cap but the group is above it, raise TIME-03 instead of MEAL-01.
- **RCP-029**: charged at 02:14 local time, so the answer is review under TIME-01 (overnight window). We pass it. *Change:* add TIME-01 as a check on `transaction_datetime`, which is already local time.

**Closest call:** the Japanese "iPad Air 11インチ 128GB" line, at confidence 0.979. The after-the-fact threshold sweep finds no change up to 0.95. At 0.98 that line is uncertain and its receipt goes to review. At 0.999, five lines and four receipts change.

## 7. The base-rate question

In this set 16 of 28 receipts (57%) should be flagged, so a false positive is rare simply because few receipts are clean. In a real stream with a 3% violation rate, a 2% false-positive rate means about 20 false flags per 1,000 receipts against 30 true ones. Two in five flags would be wrong, and reviewers would learn to ignore them. Our 0/9 is consistent with anything from 0% to 30%, so it says almost nothing about that regime. Before trusting the system, I would measure the false-positive rate and flag precision on a few thousand randomly sampled real receipts (tight enough to tell 1% from 3%). I would also check that confidence is calibrated on genuinely ambiguous items, which this data barely contains.

## Stretch items

- **Per-rule precision and recall:** 1.00 / 1.00 for every rule in the answers (`metrics.json`, `exceptions.per_rule`).
- **Exact line items:** all 26 matched exceptions cite exactly the gold lines.
- **The four timing receipts:** described in section 6.
