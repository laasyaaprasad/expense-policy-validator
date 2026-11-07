# Experiment 7: a local semantic lookup first, the model only when it isn't sure (plan)

Written before any embedding or model call, on the branch `semantic-first-v2`. Main is not changed by anything here. If the result passes, the pipeline on this branch switches to it, and merging it into main is a separate decision.

## Question

Can a semi-deterministic classifier replace the current one (the model at temperature 0) and be as safe? The proposed classifier:
- **decides a line itself** when its meaning is clear and safe, the same way every time;
- **sends everything else to the model;**
- **sends the model's own unsure lines to a human.**

Experiment 6 tested a first version. It decided 30% of lines and was right on all 72 of its decisions on the project's own sets, but failed its bar on CORD:
- "\*Rhum" and a free candle: restricted-flag errors, both on unsure labels;
- "25%": a bare number, read as a service charge;
- its hosted embeddings varied between runs.

This version changes what that run exposed. Every change is listed, so the result can be read knowing which ones came from seeing experiment 6.

## Design

**Step 0: code checks, before the lookup.** A line goes to the model if any of these holds:
1. **Recipient or item kind:** the text gates of experiment 6 (a name or title, gift, client or present; phone, prescription, event or ticket words).
2. **No real words** *(new, from "25%")*: no run of three or more letters, outside digits, such as "25%", "SB 1 OR", "K7 1217". A line with any CJK character counts as having words.
3. **Merchant** *(new)*: the merchant is one of the three kinds where the policies make context decide what an item is:
   - MCC 5921, a package store (ALC-03, retail alcohol);
   - MCC 5912, a pharmacy (prescription or over the counter);
   - MCC 5411, a supermarket (cooking wine, household stock).

   Every line on such a receipt goes to the model.

**Step 1: the semantic lookup** *(changed: local, pinned)*.
- **The embedding model:** `Qwen/Qwen3-Embedding-0.6B`, run locally with sentence-transformers on the CPU. It is the small sibling of experiment 6's best model; nothing else is tried, so the model isn't chosen on any test set. The same instruction goes on both sides: "Identify what kind of product or service this expense receipt line is". Vectors are unit length. Line text is normalised and embedded in its own language, with no translation.
- **The library** (`library.csv`, 413 lines):
  - **391 from experiment 6:** typical items and the terms the taxonomy and prompt name;
  - **22 new hard negatives** *(new)*, written from the taxonomy's and prompt's own rules: dishes and desserts made with alcohol, alcohol-free drinks and barware, which are food or household goods; and cooking sherry and mirin, which are alcohol. Their job is to make a misleading line look mixed, so it goes to the model.
    - **"Rum essence for baking" and the cooking wines were written knowing experiment 6's errors and experiment 5's miss,** so the "\*Rhum" line on CORD validation and the cooking wines on the 32 and reworded receipts are not clean tests of them. CORD's test split is.
  - **Decontamination:** dropped automatically before any call if a library line is a near-copy of any test line (experiment 6's rule: same normalised text, word overlap of 0.5 or more, or character similarity of 0.8 or more). The test lines are those of the 32 receipts, the dev set, the 64 reworded lines, and CORD's validation and test splits.
  - **Not checked by hand before this run,** at the user's choice to see the results first. Any change after the results is a new version, needing data this run didn't use.
- **Scores:** a category's score is its nearest library line's similarity. The best category wins, and its margin is the best score minus the second best.
- **It decides a line only if all of these hold, and otherwise sends it to the model:**
  1. **Restricted guard:** the 10 nearest library lines all share the chosen category's restricted flag.
  2. **The category isn't one that needs the model:** `other`, `health_otc` or `gift`, which need an explanation, an item kind or a recipient.
  3. **The margin clears the cutoff for its kind** *(new: stricter for restricted categories)*:
     - **Safe categories:** the cutoff is the lowest margin at which the library lines decided leave-one-out are at least 99% right, with no restricted-flag error.
     - **Restricted categories:** a separate cutoff, the lowest margin at which they are 100% right, and never below the safe one.

     Both cutoffs are chosen on the library alone.
- **Confidence of a decided line:** the best smoothed accuracy, (right + 1) / (decided + 2), of its kind's leave-one-out decisions with a margin of at least t, over every t from the cutoff up to the line's margin. This is experiment 6's amended rule, applied to each kind.

**Step 2: the model**, exactly as on main: the same prompt, temperature 0, the whole receipt for context. Its answers are used only for the lines the lookup didn't decide. A receipt whose every line the lookup decided needs no model call and no translation; these are counted.

**Step 3: human review**, as on main: a line below 0.5 raises EXC-03.

## Arms

- **M, main:** the current classifier on every set. The 32 receipts, the dev set and CORD validation replay the committed cache; the reworded lines and CORD test are run fresh where the cache has no entry.
- **V2:** steps 0–3, with step 2 taking M's answers for the lines it escalates.
- **V2+L, learning:** V2 with CORD validation's sure labels added to the library, standing in for human-reviewed receipts, measured on CORD test only. Its cutoffs are recomputed on the larger library by the same rule. It answers "does the lookup take more lines as reviewed receipts accumulate?"

## Test sets

1. **The 32 receipts:** all through the engine. Scored on the 28 by the Part 3 measures, plus the 4 timing receipts.
2. **The dev set:** 43 trap items.
3. **The reworded receipts:** experiment 5's checked variants, with the 4 timing receipts.
4. **CORD validation:** 100 receipts, `cord/labels.jsonl`.
5. **CORD test:** 100 receipts, `cord/test/labels.jsonl`. Nothing in the system has seen them.
6. **Repeatability:** the library and every test line are embedded again in a separate process, and the two sets of vectors compared.

## Bar (fixed now)

**V2 replaces M only if all of these hold:**
1. **28 receipts:** 28/28 exactly right, 65/65 items, 0/9 false positives, 0/16 missed violations.
2. **Timing receipts:** all 4 match.
3. **Dev set:** at least 42/43 categories, item kinds and recipients.
4. **Reworded receipts:** no fewer items or receipts right than M on the same run.
5. **CORD validation and CORD test, each:**
   - lines right at least M's minus 2;
   - false alarms no more than M's;
   - missed violations no more than M's.
6. **Safe.** On every line the lookup decides whose label is sure (the 32 receipts, dev, reworded, both CORD splits):
   - at least 99% right;
   - no restricted-flag error.

   Lines with unsure labels are reported but not counted. This exemption is decided now, after experiment 6, whose two restricted errors were both on unsure labels.
7. **Useful:** the lookup decides at least 20% of CORD validation's and test's lines together (at least 112 of 557).
8. **Repeatable:** the second embedding run gives the same vectors, to 1e-5 in every component, and so the same decisions.

V2+L is reported against V2 on CORD test (lines decided, and bars 5 and 6). It is not part of the adoption decision. Nothing is tuned on what this run shows.

## Spend

- **Embeddings:** local, $0.
- **Model calls:** M on CORD test (100 classifications and the translations), and whatever the reworded lines miss in the cache. About $0.03–0.05, in this experiment's `spend.jsonl`.
- **Downloads:** the model (1.2 GB) and PyTorch with sentence-transformers, approved by the user.

## What this can't show

- **The library was written by an assistant that has seen every test set except CORD test.** Decontamination catches copies, not meaning, so CORD test is the evidence that counts.
- **The cutoffs and confidence are calibrated on the library, not on a company's reviewed receipts.** In production they would be recalibrated on those.
- **Both CORD splits have one made-up context** (MCC 5812), so the merchant check never fires there.
- **The CORD test labels are the assistant's,** and not yet checked by hand.
