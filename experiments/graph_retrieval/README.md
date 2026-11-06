# Experiment 4: a policy knowledge graph for retrieval

**Question:** if retrieval had been built around a knowledge graph that an LLM extracted from the policy text, would it find every rule each receipt needs, as the submitted structured index does, and lead to the same decisions? And how do keyword search and embeddings, the options DECISIONS.md §6 argued against, actually do? The plan, arms and success criteria were fixed first in [PLAN.md](PLAN.md).

`make graph-retrieval` reruns it from the cached embeddings. The LLM replies aren't committed, because each prompt holds a policy rule's text, so a fresh extraction needs `OPENROUTER_API_KEY` and costs under a cent. The graphs are in [results/](results) as readable triples (`graph_extracted.tsv` and the others), with every number in [results/metrics.json](results/metrics.json).

**Answer: the graph works as a retriever, but it isn't better than the structured index, and it's only as reliable as its edges.**
- The extracted graph found every needed rule in all three settings and reached the same decisions, at 10.0 rules a receipt against the index's 9.5.
- Keyword search and embeddings couldn't get there without retrieving every rule.
- But the graph's 100% rests partly on this data. Four of its trigger edges are missing, and a constructed receipt that needs one of them loses an exception.

## Results

The unchanged engine and the hand-reviewed `rules.yaml` throughout; only the rules chosen per receipt differ. "Needed" means the rules whose check finds something when the engine runs every rule: 37 across the 32 receipts, 9 across CORD.

| Retriever | Receipts with every needed rule (oracle · system · CORD) | Rules per receipt | 28 exactly right (oracle · system) | CORD decisions as submitted |
|---|---|---|---|---|
| Structured index (submitted) | 32 · 32 · 100 | 9.5 (7–15) | 28 · 28 | 100 |
| Graph from `rules.yaml` (control) | 32 · 32 · 100 | 9.5 (7–15) | 28 · 28 | 100 |
| **Graph extracted by the LLM** | **32 · 32 · 100** | **10.0 (7–15)** | **28 · 28** | **100** |
| Graph from experiment 3's config | 31 · 31 · 100 | 10.4 (8–16) | 27 · 27 | 100 |
| BM25, top 10 | 19 · 19 · 100 | 10 | 18 · 18 | 100 |
| Embeddings, top 10 | 21 · 21 · 99 | 10 | 18 · 18 | 99 |
| Every rule | 32 · 32 · 100 | 34 | 28 · 28 | 100 |

- **The control** (the graph built from `rules.yaml`) retrieves exactly what the submitted retriever does on all 132 receipts, so the traversal is right, and any difference in the other graphs comes from their edges.
- **Two hops** along citations for the extracted graph: still every needed rule, at 10.7 rules a receipt.
- **Cost:** 34 extraction calls plus 164 embeddings, $0.0038 in all.

## What it shows

1. **The structured index already is a graph.** `rules.yaml` turned into triples reproduces it exactly: rules point to the receipt facts that trigger them and to the rules they cite. So "graph or index" is really "who writes the edges: a reviewed YAML file, or an LLM reading the policies?"
2. **The LLM wrote most edges right:**
   - **Perfect:** regions (4 / 4), carve-out references (5 / 5) and line takeovers (1 / 1).
   - **Triggers:** 42 right, 11 extra and 4 missing. The extras cost a little precision (10.0 rules against 9.5); the missing ones are the risk.
3. **Its 100% recall was partly luck.**
   - **GIFT-01 lost its "names a recipient" trigger,** but gained "client recipient", which covered RCP-018's bracelet for a client. Experiment 3's graph lacked both and missed that rule.
   - **MEAL-02, REG-02 and REG-03 lost their "Client Entertainment" trigger,** but every Client Entertainment receipt here has a food line, which also triggers them.
   - **A constructed receipt with no such line** ($200, non-itemized, Client Entertainment, no attendee count) shows the gap. The index retrieves MEAL-02 and raises "attendee count missing"; the graph never checks it. The receipt is still flagged for other rules, but its evidence is incomplete.
4. **It mixed up precedence edges:** REG-01 and REG-03 "replace" the global rules instead of "overriding" them. Retrieval doesn't use these (the engine resolves precedence from `rules.yaml`), so nothing changed here. A design where the graph also drove precedence would have been affected.
5. **Keyword search and embeddings can't do this job.**
   - **What they missed:** at 10 rules a receipt they missed the merchant-risk rules (MCC-01 and MCC-02 depend on the merchant code's risk tier, which no line text mentions) and the timing rules (TIME-01 and TIME-03 are about clock times and same-day receipts, not words).
   - **The consequence:** 18 of 28 receipts exactly right. Reaching every needed rule took all 34 rules for both, which is no retrieval at all. That measures DECISIONS §6's argument: a rule missed here is a violation missed, and text similarity can't see the structured facts that trigger half the rules.
6. **It costs more code:** 229 lines for the graph store, extraction and traversal, against the submitted retriever's 53. Some of that replaces writing `applies_to` by hand.

## Graph database or not

No graph database was used: the graph is an in-memory triple store with the two queries a graph database would run. Neo4j would hold the same triples and give the same numbers. It would add a query language and a visual browser, and pay off at thousands of rules across many legal entities and policy versions, especially for impact analysis ("which rules and past receipts does this addendum change?"). At 40 rules it would change the setup, not the results.

## What it means

- **For retrieval:** the graph matched the index here, so there was no gain from switching. The reviewed structured index stays the safer choice, because a person checked every trigger.
- **For the bigger picture:** the graph's value is in the authoring, not in retrieving.
  - **Authoring:** an LLM can draft most of the edges from the policy text, as in experiment 3.
  - **Impact analysis:** with the rules as a graph, a policy change shows which rules and past receipts it touches.
- **What the experiments say together:** LLMs extract rule content reliably and relationships mostly; the gaps sit in the trigger and precedence edges. Those gaps need a review and an end-to-end check against known answers, plus probes for receipt shapes the data doesn't cover.

## Limits

- **Small counts:** 37 needed rules on the receipts, 9 on CORD. CORD has made-up context, so its regional, timing and merchant-risk rules barely appear.
- **One extraction run** at temperature 0, with one model.
- **k = 10 for the text retrievers** was fixed in advance; the curves at other k are in `metrics.json`.
- **The probe receipt is constructed:** it shows a gap exists, not how often it occurs.
- **The shared tables** (regions, merchant-risk tiers) come from `rules.yaml` for every arm.

## Spend and data

$0.0038 through OpenRouter (34 DeepSeek calls, and embeddings served by DeepInfra), in this experiment's own [spend.jsonl](spend.jsonl). The models saw the policy text, and for the embeddings the receipts' line text, glosses, categories and context; no amounts, employee fields or labels.
