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
- **Outputs:** `outputs/cord/v1/`, including `metrics.json`.
- **Hand check:** `review/cord_labels.csv` holds every restricted and unsure label, plus a random 30 of the rest, and shows no system output.
- **Results:** in `docs/evaluation.md`, section 8.

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
