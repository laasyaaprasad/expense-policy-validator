# Experiment 1: a cascade classifier (plan)

Written before the experiment's first run. The submitted system is not changed by anything here.

## Question

Can a cheap, repeatable first stage label most receipt lines by meaning and send only the lines it is unsure of to the LLM, matching the current LLM-only classifier's accuracy and decisions at lower cost?

## Arms

| Arm | First stage | Escalates to the LLM when |
|---|---|---|
| **LLM only** (the submitted system) | none | always |
| **Embedding only** | multilingual embeddings, nearest category | never |
| **Embedding cascade** | the same | the margin is below the threshold, or a gate fires |
| **Embedding cascade, conservative** | the same | as above, or the first stage calls the line restricted |
| **TF-IDF only / cascade / conservative** | character n-gram TF-IDF, nearest category | as for the embedding arms |

The TF-IDF arms show how much of the cheap stage's work is meaning and how much is spelling.

## The cheap stage

- **Embeddings:** `intfloat/multilingual-e5-large` through OpenRouter (served by DeepInfra), with the e5 `query: ` prefix on every text. No sampling: the same text always gets the same vector, and every vector is cached in `cache/`.
- **What it matches against (prototypes):** never the 32 receipts' or CORD's labels.
  - The 21 category definitions from the classifier prompt (`validator/classifier.py`, `DEFINITIONS`), each as a whole sentence and split into its listed terms ("beer", "dry cleaning", "dishes cooked with alcohol").
  - The 43 hand-written dev items, with their expected categories.
- **Score:** for each category, the highest cosine similarity to any of its prototypes. The line takes the best category; its **margin** is the best score minus the second best.
- **Gates** (escalate whatever the margin), because the first stage only gives a category:
  - the first stage says `other`, since item kinds (phone, venue, prescription) live there;
  - the line looks like it names a person, by a pattern written from the dev set's four recipient lines only (honorifics, an initial and surname, "for/pour/para/für" followed by a name, "guest", "inspector"). Misses on the receipts are reported, not fixed.
- **Non-itemized receipts** are `other` with no model call, as in the submitted classifier.
- **Lines the first stage keeps** get confidence 1.0, so EXC-03 applies only to escalated lines, through the LLM's own confidence.

## Escalation

An escalated line takes the LLM's answer already committed from the real runs (`outputs/classifications.jsonl`, `outputs/cord/classifications.jsonl`, `outputs/dev/configured.jsonl`): $0, and identical to the submitted system's answer. The approximation: in a real cascade the LLM would see only the escalated lines, not the whole receipt.

## Threshold rule (fixed now)

Chosen on the dev set only, for each cascade arm separately, with every dev item scored leave-one-out (its own prototype removed). The candidate thresholds are 0 and every distinct dev margin. **Pick the lowest threshold at which the dev lines the first stage keeps have no wrong restricted flag and at least 95% category accuracy.** If none qualifies, every line escalates and the arm equals LLM only. The 32 receipts and CORD never choose or move it; their threshold sweeps are reported only as analysis afterwards.

## Measures

- **Lines:** category accuracy, macro F1, restricted precision, recall and F1, against the answer key (32 receipts), the dev labels, and the assistant's CORD labels.
- **Decisions:** the escalated mix goes through the unchanged retriever and engine. On the 28 scored receipts: exactly right, false positives, missed violations. On CORD: false alarms, flag precision, missed flags, against the labels' own decisions.
- **Escalation:** the share of lines sent to the LLM, and of receipts needing any LLM call.
- **Cost:** LLM tokens per call fitted from the spend ledger as a fixed part plus a part per line, priced at Fireworks' list price ($0.21 / M input, $0.84 / M output); translation only when an escalated line isn't in English, since the embeddings are multilingual; embedding cost as billed.

## What would count as the cascade working

On the 32 receipts it keeps 28 / 28 exactly right with no new false positive or missed violation, and on CORD it adds no false alarm, while escalating clearly fewer lines than all of them. A cascade that is cheaper but less accurate is reported as a trade-off, not a win.

## Limits, stated in advance

- 43 dev items choose the threshold, so it is noisy.
- The 32 receipts were seen while building the system, and CORD's labels are the assistant's (57 hand-checked).
- Reusing cached LLM answers slightly flatters the cascade: the LLM saw full receipts.
- The embedding host (DeepInfra, via OpenRouter) is a new service that sees line text.
