# CORD pilot: real receipts

**Source.** CORD v2 (Park et al., "CORD: A Consolidated Receipt Dataset for Post-OCR Parsing", 2019), [naver-clova-ix/cord-v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2), licensed [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). The receipts were photographed in Indonesian restaurants, cafés, bakeries and shops.

**What was taken.** Only the parsed text (`ground_truth`) of the 100 receipts in the validation split. No images were downloaded. The rows came from the Hugging Face datasets-server:

```
curl -o rows.json "https://datasets-server.huggingface.co/rows?dataset=naver-clova-ix/cord-v2&config=default&split=validation&offset=0&length=100"
python -I evaluation/cord_convert.py rows.json cord/receipts.jsonl
```

**What was changed.**
- **Format.** Each menu item becomes a line, followed by its sub-items. One nameless item was dropped. Prices are whole rupiah.
- **Context (synthetic).** CORD records no merchant, date, employee or expense type, so every receipt gets the same declared context (`CONTEXT` in `evaluation/cord_convert.py`):
  - a US employee, filing "Meals - Individual" for one attendee
  - MCC 5812, merchant "(not recorded)"
  - IDR at 16,000 per USD
  - one employee per receipt, on a three-night trip of its own (added with the timing rules, so TIME-02 doesn't fire on every receipt; no result changed)
- **Language.** Every line is marked `id`.
- **Totals.** Two receipts have no total, so their line sum is used.

**Run and score.** `make cord` scores the committed outputs offline. `uv run python -m evaluation.cord --run` reruns the pipeline first.
- **Outputs:** `outputs/cord/`, including `metrics.json`.
- **Hand check:** `review/cord_labels.csv` holds every restricted and unsure label, plus a random 30 of the rest, and shows no system output. **Done:** all 57 checked by hand against the guide, after the system's CORD results were known. 52 stand; 5 were corrected, none restricted: the two bare "TT" lines became `other` (nothing on their receipts says what they are), "Kwan Yin Cup" and "F.Wngr GES-HC" became sure, and "ISI CAMPUR" became unsure. The `ok` column records each verdict against the original label; `labels.jsonl` holds the corrected ones.

## Labels (`labels.jsonl`)

One row per line: `category`, `restricted` (from the taxonomy) and `sure`. The labels were written from the line text and the receipt's other lines, before the system was run on any CORD receipt. They are used only to score the system, never as input to it.

**Labelling guide (fixed before labelling):**
1. **What the item is.** Label it by what it is, the same rule as the taxonomy.
2. **Options.** A sub-line that is an option of its item (size, sugar level, ice, spice level, topping, cut, "+Hot") takes its item's category. Package contents are labelled as items.
3. **Packaging.** Packaging listed with food or drink (bags, boxes, cups, lids, carriers, plastic containers) is `other`, marked unsure. The taxonomy has no packaging category, and this is not household stock.
4. **Charges and discounts.**
   - A charge described as a charge, such as a take-away charge, is `service_fee`.
   - A discount line is `other`.
5. **Unreadable names.** A name the labeller cannot read is given a best guess from the receipt's other lines and marked unsure.
6. **Sure.** `sure` means the labeller would bet on the category, not only on the restricted flag.

## Method

Fixed in NOTES.md on 2026-10-30, before any label was written or any model call made (commit 9fc0708), and moved here. The only edits since are to how the system is named.

The 32 receipts are 57% violations, and nothing in them is unseen or messy. This pilot asks what the system does on real receipt text, at the rate restricted items actually occur.

- **Data.** The 100 receipts in CORD v2's validation split (CC-BY-4.0): real receipts from Indonesian restaurants, cafés, bakeries and shops, 270 lines after conversion. Text only.
  - The train split is too large for the API that serves the text.
  - The test split stays unseen for a later held-out check.
- **Context.** CORD has none, so every receipt gets the same declared, synthetic context: a US employee, "Meals - Individual", MCC 5812, one attendee, IDR at 16,000 per USD, one employee per receipt. Under it, the only rule that can fire without an item cause is the $100 international meal cap (MEAL-01), on CORD-V041.
- **Labels.** The assistant labels every line, sure or unsure, following the labelling guide above. The labels are committed before the first model call and never reach a prompt. The user then checks every restricted and every unsure label, plus a random 30 of the rest.
- **What runs.** The system exactly as frozen: same prompts, model, rules, and threshold 0.5. Nothing is tuned on the pilot. The correct decisions come from the engine run on the labels (oracle mode), under the same context.
- **Metrics.**
  1. **Items:** accuracy and macro F1 on all lines and on sure lines, plus restricted precision and recall.
  2. **Receipts:** false alarms are receipts the labels pass but the system flags, or sends to review; each count gets a Wilson interval. Flag precision is the share of the system's flags that the labels also flag.
  3. **Confidence:** accuracy by confidence band, and how many lines and receipts each threshold in the `SWEEP` grid would send to review.
  4. **Disagreements:** each one is listed with its raw text, gloss, system category and label.
- **Threshold rule, fixed now for a full run.** For each t in the `SWEEP` grid:
  - cost(t) = 10 × (lines at or above t whose restricted flag is wrong) + 1 × (receipts with any line below t).
  - The lowest cost wins, and ties go to the lower t. The result is also shown at weights of 5 and 20.
  - **On the pilot,** this is computed for illustration only.
  - **To be adopted,** a threshold must be chosen on the train split, checked on the unseen test split, and released as a new version.

## Results

`make cord` writes `outputs/cord/metrics.json`. The committed run is the system with the semantic lookup first (DECISIONS.md, section 12), then the model at temperature 0 through OpenRouter (Fireworks). The lookup is experiment 12's rule (every word accounted for, by meaning). It decided 80 lines, all matching the labels, and 23 receipts never reached the model; the run made 154 calls for $0.0233, about $0.00023 a receipt. The lookup's word threshold was calibrated on these receipts among others, so the lookup column is not an independent test. Before the lookup, the model ran on every line at temperature 0: 200 calls for $0.0276. The run at temperature 1 and the first run (200 calls, $0.0394) are in the other columns; their outputs are in git history.

| | Lookup first (committed) | Temperature 0, model on every line | Temperature 1, through OpenRouter | First run |
|---|---|---|---|---|
| Lines right | 255 / 270 (94%); 241 / 245 on sure labels | 252 / 270 (93%); 239 / 245 on sure labels (98%) | 250 / 270; 241 / 245 | 252 / 270; 240 / 245 |
| Restricted lines | 1 / 1 found; 9 wrongly restricted | 1 / 1 found; 9 wrongly restricted | 1 / 1 found; 9 wrongly restricted | 1 / 1 found; 7 wrongly restricted |
| False flags | 6 / 98 | 6 / 98 (95% CI 2.8–12.7%) | 6 / 98 | 6 / 98 |
| False reviews (EXC-03) | 4 / 98 | 6 / 98 | 7 / 98 | 5 / 98 |
| Flag precision | 2 / 8; 8 / 8 if the unsure labels go the system's way | 2 / 8 (7–59%); 8 / 8 if the unsure labels go the system's way | the same | the same |
| Missed flags | 0 / 2 | 0 / 2 | 0 / 2 | 0 / 2 |

The labels give 98 pass and 2 flag. At temperature 0 the system gives 86 pass, 8 flag and 6 review, and its 8 flags include both real ones. The one restricted line it found is a rice soap. 15 of the 270 lines changed category from temperature 1 to 0, almost all unreadable codes and packaging. One is a new restricted error on a sure label: "EL KEUN HWANGTAE", a Korean dried-pollock dish, read as alcohol at 0.73, on a receipt already flagged for a real violation, so no decision changed.

The six false flags come from seven lines. Three plastic or paper bags and a cup with its seal lid were read as `household_goods`, and so was a free birthday candle. "*Rhum", an option on a bakery item, was read as a spirit. Every one has an unsure label. If every unsure label went the system's way (an after-the-fact check), all 8 flags would be right. So the flag precision turns on whether packaging is `household_goods`, and neither the taxonomy nor HOUSE-01 settles that.

The six reviews are EXC-03 on lines below 0.5: unreadable codes ("Serbu 1", "REGULAR", "CHARGE TA"), a drink option ("Less Ice"), packaging ("MIKA KECIL", a small plastic tub) and a cake ("MARBLE CASTELA"). "TALAM UNGU", a purple steamed cake, was glossed "PURPLE TRAY": on the first run it was flagged as kitchenware at 0.99; through OpenRouter the same gloss is read as food, at 0.48 at temperature 1 and 0.51 at 0. Its receipt is still reviewed, for the plastic tub. That is EXC-03 doing its job, but the labels can never send a receipt to review, so they count against the system.

Confidence ranks errors but isn't calibrated. The 207 lines at 0.999 or above are 99% right and the 63 below are 75% right, yet some confident errors sit at 0.9–1.0: the bags at 0.92–0.99, "*Rhum" at 0.97, the cup at 1.0. For illustration, the threshold rule picks 0.98 at the lowest weight and 0.99 at the other two. At 0.99 false flags would fall from 6 to 1, but 35 of 100 receipts would go to review. The system stays at 0.5.

The first attempt crashed on CORD-V048, the 49th receipt. The API's logprob tokens ended with a newline its reply text lacked, and one of the two failure paths in `category_confidences` raised instead of returning confidence 0. Both are fixed, and the 32 receipts' outputs are byte-identical after the fix.

## Why nothing was tuned on it

A second version built from these errors (packaging guidance, skipping $0 lines, dish-name glosses) was started and stopped before any of its code was written. It would have fitted the system to Indonesian café and bakery receipts, which the brief isn't about, and on this track the brief fixes the taxonomy ("ignore the file's own invitation to add categories"). The test split was downloaded but never committed or run. What the pilot leaves behind is the crash fix and one open question: should a free ($0) line raise an item exception?
