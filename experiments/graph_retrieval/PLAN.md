# Experiment 4: a policy knowledge graph for retrieval (plan)

Written before the experiment's first model call. It runs on the branch `experiment/graph-retrieval`. The submitted system, `rules/rules.yaml` and every committed output stay unchanged.

## Question

If retrieval had been built around a knowledge graph extracted from the policy text, would it find the rules each receipt needs as reliably as the submitted structured index, and lead to the same decisions? And how does it compare with the other retrieval options DECISIONS.md §6 argued against (keyword search, embeddings) but never measured?

## What retrieval has to do

For each receipt, pick which of the 34 checkable rules the engine runs (mechanics never run). A rule it misses is a violation the engine misses, so **recall must be 100%**. Fewer rules retrieved is better only after that. Today's structured index retrieves 9.5 rules a receipt on average (7 to 15).

**The rules a receipt needs:** the rules whose check finds something (raised, review signal or waived) when the unchanged engine runs every rule on that receipt. This is computed separately for oracle mode (the answer key's categories), system mode (the committed classifications) and CORD.

## The graph

- **Nodes:** the rules, and the facts a receipt can have: each category, item kind, recipient type, expense type, region and merchant-risk tier, plus "a line names its recipient" and "any receipt".
- **Edges:**
  - **triggered by** (rule → fact)
  - **restricted to** (rule → region)
  - **cites** (rule → rule)
  - **overrides** (rule → rule)
  - **replaces** (rule → rule)
  - **takes lines from** (rule → rule)
  - **waived by** (rule → reference kind)
- **Storage:** an in-memory triple store, with the queries a graph database would run (neighbours by edge type, filtered by node). A graph database such as Neo4j would hold the same triples, but there's no Docker or Neo4j on this machine, and at 40 rules it would change setup, not results. Loading the triples into Neo4j is an optional extra if you install Docker.

## Retrieval by graph traversal

1. Start from the receipt's fact nodes: its lines' categories, item kinds and recipient types, "names a recipient" if any line does, its expense type, its merchant's risk tier, and "any receipt".
2. Take every rule with a **triggered by** edge to one of them.
3. Keep a rule only if it has no **restricted to** edge or is restricted to the employee's region.
4. Follow **cites** one step out from the rules matched on a specific fact (not from "any receipt"), keeping the region filter. This mirrors the submitted retriever.

Two-step traversal is reported as a side analysis.

## Arms

| Arm | How rules are chosen | Built from |
|---|---|---|
| **Structured index** (submitted) | `validator/retriever.py` | `rules.yaml`, as reviewed |
| **Graph, extracted** (the experiment) | graph traversal | **an LLM reading the policy text only**: one call per rule asking for its triples |
| **Graph, from experiment 3** | graph traversal | experiment 3's retrieval-variant configs, turned into triples (no new calls) |
| **Graph, from `rules.yaml`** (a control) | graph traversal | `rules.yaml` turned into triples; should equal the structured index, which checks the traversal itself |
| **Keyword search (BM25)** | the top 10 rules by BM25 over the rule texts | the policy text |
| **Embeddings** | the top 10 rules by cosine similarity | the policy text, with experiment 1's embedding model |
| **Every rule** | all 34 | a ceiling for recall |

- **The query for BM25 and embeddings** is the receipt's line texts and glosses, expense type, merchant category and region.
- **k = 10** is fixed in advance, about the structured index's average. Recall at every k is reported afterwards as analysis.

**The extraction (graph, extracted):**
- **Input:** DeepSeek V4.1 Flash through OpenRouter gets each rule's text, the allowed node values and one made-up example, and returns triples as json.
- **Validation:** the reply is checked against the allowed nodes and edge types, with one retry.
- **Never shown:** `rules.yaml`, the answer key or any receipt.
- **Settings:** temperature 0, cached, with spend in this experiment's own `spend.jsonl`.
- **Cites:** these edges come from the parser (`validator/policies.py`), a deterministic reading of the text.

## Measures

1. **Recall**, the headline: the share of needed rules retrieved, and the receipts with every needed rule retrieved, in oracle mode, in system mode (28 receipts) and on CORD (100).
2. **Rules retrieved per receipt** (precision), the mean and the range.
3. **Decisions:** the unchanged engine and the hand-reviewed `rules.yaml` with each retriever.
   - **The 28 receipts:** exactly right, in system and oracle mode.
   - **CORD:** decisions matching the submitted run.
   - This isolates retrieval: only the rules chosen differ.
4. **Graph quality** (extracted arms): precision and recall of each edge type against the triples from `rules.yaml`.
5. **Cost and effort:** calls, dollars, and lines of code.

## What would count as the graph working

The extracted graph reaches 100% recall in all three settings and the same decisions as the structured index, with no more rules per receipt. Matching on decisions with lower recall counts as "lucky", not "working": a needed rule that happened not to change a decision here would elsewhere.

## Limits, stated in advance

- **This builds only the retrieval layer.** The engine still decides; a graph can't compute caps or totals.
- **Shared tables** (regions, merchant-risk tiers) come from `rules.yaml`, as in experiment 3.
- **The receipts were seen** while building the submitted system, and CORD's context is made up, so its regional and timing rules never come into play.
- **k = 10 favours BM25 and embeddings.** It is about the average the structured index needs, and they have no way of knowing per receipt how many rules it should be.
