# Evaluation (Part 3)

Every number here comes from `make eval`, which scores the committed outputs on the 28 in-scope receipts and writes [`outputs/metrics.json`](../outputs/metrics.json); the viewer's Metrics page shows the same file. Intervals are 95% Wilson intervals. Matching follows the data dictionary. An exception matches on receipt + `rule_id`, and its evidence is correct when the `line_ids` are exactly equal. A signal matches on receipt + `rule_id`.

**The first scored run after the config freeze, and final.** The prompt, threshold, rules and model were committed and tagged `frozen` before any real receipt was classified, and that first run was committed before it was scored. Nothing that affects these scores was changed after seeing a gold error, so these are the final numbers. The later changes, the timing rules (section 6) and fixes found in review, changed no decision on the 28. No classification or decision on the receipts was seen before the freeze; the 11 translations were made and signed off by hand just before it. That is provenance, not independence: the assistant that wrote the prompt and the engine had the answer key to hand, and the engine was checked against it in oracle mode before the freeze (section 1).

**The submitted run: the same config through OpenRouter.** That first run used DeepSeek's own API. Once the brief's OpenRouter key arrived, the frozen config was rerun unchanged through OpenRouter, pinned to the Fireworks host, on the same model. All 79 categories and all 32 decisions came out the same, so every number in sections 1 to 5 holds for both runs; the committed outputs are OpenRouter's. Confidences moved slightly (the lowest scored line went from 0.979 to 0.900), and on the dev set and CORD a few unclear lines changed (sections 6 and 8).

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
- The prompt and dev set were written by an assistant that had the receipts and their answer key to hand, after building the engine on the gold categories. So 65/65 is not a forecast for new receipts.

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

The system gets none of the 28 in-scope receipts wrong, so the blame report (`outputs/blame.json`) is empty. Its tests plant one fault per stage to show it names the right one. So both cases come from outside the scored set: one from the four unscored timing receipts, which the frozen run got wrong, and one from the CORD receipts (section 8).

- **RCP-020 / RCP-021, rule logic (since fixed).** One employee's $74.00 lunch and $71.50 dinner at the same restaurant on the same day. Each is under the $75 meal cap; together they are over it. The frozen run raised MEAL-01 on both, but the answer is TIME-03 (split transactions), which replaces the cap it splits around. It also passed RCP-029, charged at 02:14 (TIME-01, overnight), and RCP-030, an individual meal with no trip record (TIME-02); both should go to review. The blame report named rule logic for all four, since oracle mode got them wrong too. *Change, now made:* TIME-01 and TIME-02 are single checks on the transaction time and the trip record. TIME-03 groups one employee's receipts by merchant name and date; when each is within a limit (the meal cap per person, the $75 itemization threshold or the $50 gift cap) but together they are over it, it raises TIME-03 on each, in place of the meal cap where that is the limit split. The rules were written from the policy text and tested on hand-written cases before the four receipts were rescored, and all four now match. That is not an independent result, since their answers were known. TIME-04 can't apply: trip records hold no dates.
- **CORD-V008, translation.** "TALAM UNGU" is a purple steamed cake. The translation call glossed it word for word as "PURPLE TRAY". On DeepSeek's API the classifier, reading both, made it `household_goods` at 0.991; that category is restricted, so HOUSE-01 flagged a receipt the labels pass. Through OpenRouter it read the same gloss as `food_beverage`, but at 0.48, so EXC-03 sends the receipt to review instead: still a false alarm, now a softer one. *Change:* the translation prompt should gloss a dish name as the dish ("talam ungu, a purple steamed cake"), not word by word, and the change would be tested on the dev set first. It was not made, since nothing is tuned on CORD.

**Closest call:** "Cooking wine - Marsala 375ml" (RCP-014), still `alcohol` but at 0.900 (on DeepSeek's API the lowest was the Japanese iPad line, at 0.979). The after-the-fact threshold sweep finds no change up to 0.9. At 0.95 that line is uncertain and its receipt is no longer exactly right. At 0.999, three lines and three receipts change.

## 7. The base-rate question

The base rate doesn't change the false-positive rate, which is measured on clean receipts; it changes how many of the flags are false. Here 57% of receipts should be flagged, so even a 2% false-positive rate would produce few false flags. In a real stream with 3% violations, the same rate gives about 20 false flags per 1,000 receipts against 30 true ones, so two in five flags would be wrong, and our 0 / 9 (interval 0–30%) can't rule out far worse. Before trusting the system I would measure the false-positive rate and flag precision on a few thousand randomly sampled real receipts, enough to tell 1% from 3%, and check that confidence is calibrated on genuinely ambiguous items, which this data barely contains.

## 8. Real receipts

The 32 receipts can't answer section 7. To get more data, the system was run, exactly as frozen, on 100 real receipts from CORD v2: Indonesian cafés, bakeries and shops, CC-BY-4.0, with every line labelled. Through OpenRouter it got 250 of 270 lines right (93%, and 98% of the lines with a sure label) and caught both real violations. Against the labels it also flagged 6 of the 98 clean receipts and sent 7 more to review (13 false alarms), so 2 of its 8 flags were right. All six false flags turn only on lines whose label is itself unsure (bags, a cup and lid, a free candle, a rum option), where the taxonomy has no clear category, and if those labels went the system's way all 8 flags would be right. The steamed cake glossed as "purple tray" is now a review, not a flag (section 6). DeepSeek's API did slightly better on lines (252) and alarms (11); 13 of the 270 lines changed category between the two, almost all unreadable codes and packaging. The confident errors sat at 0.9–1.0, which a threshold can only catch by sending about a third of receipts to review (30 at 0.99). That is why the threshold should be calibrated on the company's own receipts. Nothing was tuned on CORD. Three limits: the assistant wrote the labels, and I hand-checked only the 57 the method names (every unsure or restricted one and a random 30), after the results were known, correcting 5 and no restricted label; only one of the 270 lines is restricted; and every receipt has the same made-up context, so the cross-receipt, regional and timing rules never come into play. The method and full results are in [`cord/README.md`](../cord/README.md).

## Stretch items

- **Per-rule precision and recall:** 1.00 / 1.00 for every rule in the answers (`metrics.json`, `exceptions.per_rule`).
- **Exact line items:** all 26 matched exceptions cite exactly the gold lines.
- **The four timing receipts:** built after the frozen run; all four now match (section 6).
