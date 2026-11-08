# Experiment 12's blind trap set

**Who wrote it:**
- **The lines:** `lines.json` was written by a fresh agent. It was told not to open the lookup's code, its memory, the experiments, the CORD receipts or the web. It saw only the category list and a description of the receipts: Indonesian-style café, restaurant and minimart lines. It was asked to be "genuinely sneaky" about what a system matching against thousands of food lines would wrongly match.
- **The labels:** every label was checked by the assistant.
- **What was committed first:** the receipts and labels were committed with the plan, before any model or embedding saw them.

**What it holds:**
- **20 receipts, 79 lines:**
  - **57 restricted look-alikes** across all ten restricted categories, such as "SOJU PEACH 360ML", "Kahlua Milk", "BODY SCRUB KOPI 200G", "AirPods Case Mochi" and "LOGAM MULIA ANTAM 5GR";
  - **22 safe look-alikes**, such as "BIR PLETOK BETAWI", "Brandy Snaps 6pcs", "Permen Rokok Coklat" and "Kue Cincin".
- **The receipts:** MCC 5812 and no merchant name, four lines to a receipt ([build.py](build.py)).

**Dropped before any model saw them** (listed in `build.py`):

| Line | Why |
|---|---|
| BINTANG ZERO 0.0% 330ML | Ambiguous: non-alcoholic by the taxonomy, but a reviewer could read it as beer |
| Bintang Radler Lemon, MARLBORO ICE BURST, TOGEL 4D | Copies of experiment 11's traps, which the design has seen |
| PENNE ALLA VODKA | A copy of a line in the lookup's curated library |
| ONION RINGS | A copy of a CORD training line |

**Checked before committing, without any model:**
- **The existing code checks** match 1 of the 57 restricted lines ("Gift Voucher Dimsum 100K", on the word "gift") and none of the safe ones.
- **The project's restricted word list** matches 8 of the 57 restricted lines and 11 of the 22 safe ones. Version 4 doesn't use the word list.

**Limits:**
- **It's a model's idea of a sneaky receipt,** not a person's.
- **The labels are the assistant's.**
