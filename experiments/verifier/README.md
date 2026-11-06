# Experiment 2: a decision model as a yes/no verifier

**Question:** the submitted system's false flags on real receipts come from lines it confidently calls restricted when they aren't. Can a second, cheap model that answers one yes/no question about each restricted line remove those flags without clearing real violations? Plan, success criteria and the switch from Jev to Mammoth-1 (made before any data call, because Jev needs paid Vercel credits): [PLAN.md](PLAN.md). Rerun offline with `make verifier`; per-line answers in [results/lines.csv](results/lines.csv), all numbers in [results/metrics.json](results/metrics.json).

**Answer: no, not as specified.** On the 28 scored receipts it agreed with every real violation, so nothing broke. But on real receipts it doubted the one true violation and backed the worst false flag, so neither variant met the bar set in advance.

## Results

**Model:** `baseten/mammoth-1` (27B decision model) through Vercel AI Gateway's Decision API, one `predicate` question per line the classifier called restricted.

| | 28 receipts: exactly right · missed | CORD: false flags | CORD: false reviews | CORD: missed flags | CORD flag precision |
|---|---|---|---|---|---|
| As submitted | 28 / 28 · 0 / 16 | 6 / 98 | 7 / 98 | 0 / 2 | 2 / 8 |
| Doubt → review | 28 / 28 · 0 / 16 | 4 / 98 | 9 / 98 | **1 / 2** | 1 / 5 |
| Veto → runner-up | 28 / 28 · 0 / 16 | 6 / 98 | 7 / 98 | 0 / 2 | 2 / 8 |

**How well its yes-probability separates real from false restricted lines:**
- **28 receipts and dev set:** all 39 lines are really restricted, and it said yes to every one (0.98 to 0.999). It cleared nothing that it shouldn't have.
- **CORD:** 10 lines, 1 really restricted. It scored the real one *below* 5 of the 9 false ones (AUROC 0.33, worse than chance on a tiny sample).

## What it shows

1. **Safe where the data is clean.** On the brief's receipts it confirmed all 21 restricted lines, including the traps that matter (Marsala cooking wine 0.996, iPad in Japanese 0.98). So adding it to the submitted system would not have broken any scored decision.
2. **It caught some packaging.** "CUP 14 OZ" (0.16), "TUTUP SEAL" (0.22), "Large Box" (0.05) and "PAPER BAG BESAR" (0.40) fell below 0.5, which turned two false flags into reviews.
3. **It missed the key trap.** "*Rhum", rum flavouring on a bakery receipt, got 0.94 yes. The instruction carried the taxonomy's own rule that dishes cooked with alcohol are food, and it still said alcohol.
4. **It doubted a real violation, apparently because of context.** "Sabun Beras", glossed "Rice Soap", is a toiletry, and DeepSeek said so at 1.0. The verifier gave 0.31, most likely because CORD's made-up context calls every receipt a restaurant meal; the instruction says context never overrides the item. In the doubt variant that turned CORD's one correct flag into a review: a new missed flag.
5. **Veto did nothing.** Only one line fell below 0.1 ("Large Box"), and its receipt stayed flagged for its other line.

## What it means

A second model checking the first only helps if it is *better calibrated on the cases the first gets wrong*. Here it agreed with the classifier on the confident errors (*Rhum) and disagreed on a confident correct answer (Sabun Beras), so it moved review load around without improving decisions. The honest next tests, not run:
- **The same check with Jev itself,** once paid credits exist, since it was the model the plan was designed for.
- **Asking without the receipt context,** to see whether the restaurant framing caused the Sabun Beras miss.
- **More real violations to test against:** CORD has one restricted line, so no rate here is reliable.

## Spend and data

49 questions for $0.0011 (about 220 input tokens each, more than the $0.0006 planned), plus earlier probes and refused test calls, all in [spend.jsonl](spend.jsonl). A median answer took 0.38 s; the run took about 10 minutes because the free tier allows 5 requests a minute. **Vercel** (the gateway) and **Baseten** (the model host) received the line text, glosses, merchant, merchant category and expense type of the checked lines; no amounts, employee fields or labels.
