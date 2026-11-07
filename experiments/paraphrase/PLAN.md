# Experiment 5: the scored receipts reworded (plan)

Written before any call on the reworded lines. The submitted system is not changed by anything here.

## Question

The classification prompt and the dev set were written by an assistant that had the 32 receipts and their answer key to hand (`docs/evaluation.md`, section 1). So the 65 / 65 on the scored items could come partly from the prompt recognising these exact item names rather than understanding the items. If the same items are reworded, does the frozen classifier still get them right, and does the unchanged engine still reach the same decisions?

## Design

- **Which lines:** the 64 itemized lines on the 28 scored receipts. RCP-023's one line is a non-itemized total, which never reaches the model, so it is left as it is.
- **The rewording** (`review.csv`), one variant per line, in the line's own language:
  - **paraphrase:** the same item, or a close equivalent of the same kind, in other words or another brand ("Sony WH-1000XM5 wireless headphones" → "Bose QuietComfort Ultra headphones", "Grilled salmon entree" → "Seared trout main"). None of the original wording is kept where it can be avoided.
  - **same trap:** where the original is built to mislead, a different item of the same category with the same kind of misleading name, so the test is no easier than the original ("Rum cake slice" → "Bourbon pecan pie slice", "Arnold Palmer" → "Virgin Mary"). Four lines, all on RCP-004.
  - **Kept as they are:** every fact a rule reads besides the category: recipient names and firms (RCP-018, RCP-022), over the counter rather than prescription (RCP-028), the gift card's value, and wine, beer or cooking wine named as such or by region, grape or brand. Amounts, merchant, MCC, expense type, attendees, trip and reference are untouched.
- **Checked by hand before the run:** every row of `review.csv` must be marked `ok = yes` by a person, who checks that the variant is the same kind of item, in the same category, and no easier. The run refuses to start otherwise. The variants were drafted by the same assistant that wrote the prompt, so this check is what stops its blind spots carrying over.
- **The run:** the frozen classifier, prompt and threshold, unchanged (`validator/classifier.py`, `classify`), with translation for the non-English lines as usual. The engine reruns on all 32 receipts: the 28 reworded ones plus the committed classifications of the four unscored timing receipts, so cross-receipt rules see the same receipts as before. Scored against the same answers, on the same measures as Part 3.
- **Caching and spend:** every call is cached under `cache/`, so reruns are offline, and logged to this experiment's `spend.jsonl`. About 28 classification calls and 8 translation calls, under $0.01.

## How the result is read (fixed now)

- **No sign of overfitting to the wording** if the reworded run loses at most 3 of the 65 items (keeps at least 62, about the dev set's error rate) and at most 2 of the 28 receipts (keeps at least 26 exactly right).
- **Otherwise, evidence that part of 65 / 65 came from the exact wording.** That is reported as the result, not fixed: nothing in the prompt or engine is changed on what this run shows.
- Either way, every miss is listed with the stage that caused it (translation, classification, or a rule), and lines whose confidence falls below the 0.5 threshold are counted, since each one sends its receipt to review.

## What this can't show

- It tests the wording, not the receipts: the amounts, merchants and situations are the same 28, so it says nothing new about false-positive rates on other kinds of receipt.
- The variants were written knowing the categories, by the assistant that wrote the prompt. A blind set written by someone else is still the stronger test.
