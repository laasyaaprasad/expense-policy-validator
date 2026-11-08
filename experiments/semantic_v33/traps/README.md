# Experiment 11's look-alike trap set

Built by [build.py](build.py), which holds every line and its label. The receipts and labels were written by the assistant and committed before any model or embedding saw them.

- **22 receipts, 88 lines,** four to a receipt.
- **66 restricted look-alikes** across all ten restricted categories. They are written to resemble the lookup's memory, which is almost all Indonesian café and restaurant food:
  - "Night cream Aloe Vera" against "Cream [L]";
  - "Pillow case brown" against "Pillow Choco";
  - "Soju Peach" against "Peach Iced Tea";
  - local and brand names no word list holds, such as "Sampoerna Mild 16", "Anggur Merah Orang Tua" and "Wardah Lightening Serum".
- **22 safe look-alikes:** food and drink whose names sound restricted, such as "Ginger ale", "Chocolate cigar", "Bir pletok" (a non-alcoholic Betawi drink) and "Sake-glazed salmon".
- **Every receipt has MCC 5812 and no merchant name,** like CORD's. So the merchant gives the lookup no help: the hardest case.
- **Every label is sure.** Ambiguous items were left out.
- **None copies a line** from CORD, the 32 receipts or the lookup's library (checked before committing).

**Checked before committing, without any model:**
- **The project's restricted word list** (`rules/keywords.yaml`) matches 17 of the 66 restricted lines, so 49 depend on the embedding rules alone. It also matches 12 of the 22 safe lines.
- **The existing code checks** match none of the 88 lines.

**Limits:**
- **The writer knew the design being tested.** The traps were aimed at the looseness experiments 9 and 10 found, not chosen to pass version 3.3.
- **88 lines is a targeted safety test,** not a sample of real spending.
