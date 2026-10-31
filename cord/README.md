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
  - one employee per receipt
- **Language.** Every line is marked `id`.
- **Totals.** Two receipts have no total, so their line sum is used.

**Run and score.** `make cord` scores the committed outputs offline. `uv run python -m evaluation.cord --run` reruns the pipeline first.
- **Outputs:** `outputs/cord/`, including `metrics.json`.
- **Hand check:** `review/cord_labels.csv` holds every restricted and unsure label, plus a random 30 of the rest, and shows no system output. This check hasn't been done yet.

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

Fixed in NOTES.md on 2026-10-30, before any label was written or any model call made (commit 97a62c6), and moved here. The only edits since are to how the system is named.

The 32 receipts are 57% violations, and nothing in them is unseen or messy. This pilot asks what the system does on real receipt text, at the rate restricted items actually occur.

- **Data.** The 100 receipts in CORD v2's validation split (CC-BY-4.0): real receipts from Indonesian restaurants, cafés, bakeries and shops, 270 lines after conversion. Text only (`cord/README.md`).
  - The train split is too large for the API that serves the text.
  - The test split stays unseen for a later held-out check.
- **Context.** CORD has none, so every receipt gets the same declared, synthetic context: a US employee, "Meals - Individual", MCC 5812, one attendee, IDR at 16,000 per USD, one employee per receipt. Under it, the only rule that can fire without an item cause is the $100 international meal cap (MEAL-01), on CORD-V041.
- **Labels.** The assistant labels every line, sure or unsure, following the guide in `cord/README.md`. The labels are committed before the first model call and never reach a prompt. The user then checks every restricted and every unsure label, plus a random 30 of the rest.
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

`make cord` writes `outputs/cord/metrics.json`. The run took 200 calls and cost $0.0394, about $0.00039 a receipt. Translation is a third of that, because every line gets glossed.

| | System on 100 CORD receipts |
|---|---|
| Lines right | 252 / 270 (93%); 239 / 244 on sure labels (98%) |
| Restricted lines | 1 / 1 found; 7 wrongly restricted (1 on a sure label) |
| False flags | 6 / 98 (95% CI 2.8–12.7%) |
| False reviews (EXC-03) | 5 / 98 |
| Flag precision | 2 / 8 (7–59%); 8 / 8 if the unsure labels go the system's way |
| Missed flags | 0 / 2 |

The labels give 98 pass and 2 flag. The system gives 87 pass, 8 flag and 5 review, and its 8 flags include both real ones. The one restricted line it found is a rice soap.

The six false flags come from seven lines, since CORD-V008 has two. Two plastic bags, a paper bag and a plastic clamshell box were read as `household_goods`, and so was a free birthday candle. "*Rhum", an option on a bakery item, was read as a spirit. "TALAM UNGU", a purple steamed cake, was glossed literally as "PURPLE TRAY" and called kitchenware at 0.99. Four of the six receipts are flagged only for lines priced at $0.

Only the cake has a sure label. If every unsure label went the system's way (an after-the-fact check), all 8 flags would be right. So the flag precision mostly turns on whether packaging is `household_goods`, and neither the taxonomy nor HOUSE-01 settles that.

The five reviews are EXC-03 on lines below 0.5: three unreadable codes ("TT", "SB 1 OR", "KENTHIR 2") and packaging on two receipts. That is EXC-03 doing its job, but the labels can never send a receipt to review, so they count against the system.

Confidence ranks errors but isn't calibrated. The 225 lines at 0.999 or above are 99% right and the 45 below are 64% right, yet the confident errors sit at 0.96–1.0: the bags at 0.96–0.98, the cake at 0.99, the box at 1.0. For illustration, the threshold rule picks 0.995 at all three weights. False flags would fall from 6 to 1, but 30 of 100 receipts would go to review. The system stays at 0.5.

The first attempt crashed on CORD-V048, the 49th receipt. DeepSeek's logprob tokens ended with a newline its reply text lacked, and one of the two failure paths in `category_confidences` raised instead of returning confidence 0. Both are fixed, and the 32 receipts' outputs are byte-identical after the fix.

## Why nothing was tuned on it

A second version built from these errors (packaging guidance, skipping $0 lines, dish-name glosses) was started and stopped before any of its code was written. It would have fitted the system to Indonesian café and bakery receipts, which the brief isn't about, and on this track the brief fixes the taxonomy ("ignore the file's own invitation to add categories"). The test split was downloaded but never committed or run. What the pilot leaves behind is the crash fix and one open question: should a free ($0) line raise an item exception?
