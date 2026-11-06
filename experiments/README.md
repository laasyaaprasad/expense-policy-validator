# Experiments

Alternatives tried after the config was frozen. Each has a plan written before its first run, its own results and spend, and changes nothing in the submitted system or its scores.

| # | Experiment | Question | Result |
|---|---|---|---|
| 1 | [Cascade classifier](cascade/README.md) | Can a cheap embedding stage handle the easy lines and escalate only uncertain ones to the LLM? | Same accuracy as LLM only, but it escalated 81% of receipt lines (9% cheaper) and 99% of real ones, and it turned one uncertain real line into a false flag |
| 2 | [Decision-model verifier](verifier/README.md) | Can a second model's yes/no check on restricted lines remove false flags without clearing real violations? | Run with Baseten's Mammoth-1 (Jev needs paid credits). It confirmed all 21 real violations on the receipts, but on CORD it backed the worst false flag (*Rhum) and doubted the one real violation, so neither variant met the bar |
| 3 | [Policy text to config](policy_config/README.md) | Can an LLM pipeline fill the standardized config from the policy text, so the unchanged engine reaches the same decisions? | Every number, quote and reference kind came out exactly right, but a few design fields (when a rule triggers, its scope) differed; in every variant one changed a decision or stopped the engine. The config tests failed on every variant, but one decision-changing error passed them and showed only end to end. Works as a draft generator behind the tests, an end-to-end check and a human review |
| 4 | [Policy knowledge graph for retrieval](graph_retrieval/README.md) | Would a graph extracted from the policy text retrieve the rules each receipt needs as well as the structured index, and how do BM25 and embeddings compare? | The extracted graph matched the index (every needed rule, same decisions, 10.0 against 9.5 rules a receipt), but a constructed receipt exposed a missing trigger. BM25 and embeddings at top 10 got 18 of 28 receipts right |
