# Experiment 3: an LLM pipeline from policy text to the standardized config

**Question:** `rules/rules.yaml` was drafted once by an AI assistant and reviewed by hand. Can a repeatable pipeline (split the policies into rules, have an LLM fill the config schema per rule, check the result) produce a config the unchanged engine can run, with the same decisions? Design, variants and what counts as success were fixed first in [PLAN.md](PLAN.md). `make policy-config` reruns it. Its cache isn't committed, because every prompt holds the full text of a policy rule (the organisers' data), so a fresh run needs `OPENROUTER_API_KEY` and costs about $0.03. The generated configs are in [results/](results) (`rules_plain.yaml` and the others), with every field difference in [results/metrics.json](results/metrics.json).

**Answer: nearly, but not unattended.** The LLM copied every number, quote and reference rule exactly. Its mistakes were in the fields that encode design decisions (when a rule triggers, whether it concerns the whole receipt, which rule replaces which), and in every variant at least one such mistake changed a decision or stopped the engine. The existing config tests failed on every variant, so none would have been merged, but one decision-changing mistake (GIFT-01's trigger) passed every test and showed only in the end-to-end run. So the pipeline works as **a draft generator behind the same tests, an end-to-end check and a human review**, not as a replacement for the review.

## Results

DeepSeek V4.1 Flash, one call per rule for the 40 rules; 201 calls for $0.032.

| Config | Rules with every field right | Config tests passed | Oracle mode (28) | System mode (28) | CORD decisions unchanged (100) |
|---|---|---|---|---|---|
| Hand-reviewed `rules.yaml` (reference) | 40 | 10 / 10 | 28 | 28 | 100 |
| 1. Plain (rule text only) | 31 | 8 / 10 | 27 | 26 | 99 |
| 2. Retrieval (+ cited and citing rules) | **33** | 9 / 10 | engine stops | engine stops | 100 |
| 3. Self-consistency (3 samples of 2, majority) | 31 | 9 / 10 | engine stops | engine stops | 100 |
| 2 or 3, with TECH-04's scope copied back (diagnosis) | | | 28 | 27 | 100 |

**Field by field** (out of 40 rules; plain / retrieval / self-consistency):
- **Perfect in all three:** `params` (every threshold), `evidence` (every quote verbatim), `requires_reference` (every carve-out's reference kind), `line_overrides`.
- **Nearly:** `kind` 39 / 40 / 39, `severity` 39 / 40 / 39, `overrides` 40 / 40 / 39.
- **Where it differs:** `scope` 37 / 39 / 38, `replaces` 38 / 37 / 36, and above all `applies_to` (when a rule triggers): 33 / 36 / 34.

## What went wrong, and why it matters

1. **A wrong scope stopped the engine, safely.** Retrieval and self-consistency called TECH-04 (software without manager approval) a whole-receipt rule. Its check cites the software line, so the engine's EXC-04 guard refused to report it, rather than give evidence that breaks the policy. The config test "whole-receipt scope matches EXC-04" had already failed on the same rule, so this config would never have been merged. The plan's lenient repair only fixes `kind`, so it couldn't help. A diagnosis run copying just that one scope back shows the rest of the config is sound.
2. **A wrong trigger lost a real violation.** In all three variants, GIFT-01 (the $50 client-gift cap) was set to trigger on the `gift` category only. RCP-018's sterling silver bracelet for a client is `jewelry`, so in system mode the retriever never checked GIFT-01 and the receipt lost its exception. Oracle mode runs every rule, so it didn't notice; this is exactly why the end-to-end checks run with retrieval.
3. **A definition was read as a mechanic.** Plain called MEAL-01, the daily meal cap, a mechanic, probably because its text also defines "meal receipt" for other rules. So the cap never ran: RCP-015 and one CORD receipt changed. Retrieval fixed it, because the rules that cite MEAL-01 show it is a cap.
4. **Some differences are defensible readings, with no decision changed here.**
   - **TIME-03 "replaces" more rules** (DOC-01 and GIFT-01 as well as the meal caps), a literal reading of "it replaces the threshold rule being split around".
   - **DOC-03 lists every expense type** instead of "always", which has the same effect.
   - **Self-consistency swapped REG-01's `overrides` for `replaces`.** No decision changed on these receipts, but an APAC receipt at a liquor store would then also get the global ALC-03.
5. **Self-consistency didn't earn its cost.** Three samples cost three times as much and scored no better than retrieval alone. Leaving out `evidence` (whose flags only mean the samples picked different fair quotes), it flagged 25 fields. That caught 9 of the 15 wrong ones, but 16 flags were on right fields, and it missed the six wrong fields all three samples agreed on: DOC-04's four fields (called a mechanic), GIFT-01's trigger and TIME-03's `replaces`.

## What it means for the design

- **What an LLM pipeline does well:** the content of the rules (every number, quote and reference kind), with retrieval along the citation graph helping the cross-rule fields.
- **What still needs a person:** the fields that are design conventions, especially `applies_to`, `scope` and `replaces`. They are few, but each can change a decision.
- **What makes it safe:** the existing config tests and the engine's guards. They caught the scope error in every variant before any decision. The gaps they didn't catch (GIFT-01's trigger, MEAL-01 as a mechanic) were caught only by the end-to-end run against known answers.
- **So the production shape is:** a policy change → the pipeline drafts config entries with retrieval → the config tests and an end-to-end run on labelled receipts gate it → a person reviews the diff of design fields → merge. That is the "LLM produces the standardized config, code applies it" design, with the evidence for where its boundary sits.

## Limits

- **The reference was itself LLM-drafted,** then reviewed, and partly checked against the answer key. "Differs from `rules.yaml`" means "differs from the reviewed version"; item 4 lists differences that are arguably fine.
- **The model was told each rule's parameter names** (the code's interface). A pipeline that also wrote the Python checks is not tested here.
- **The shared tables were copied, not extracted.** That's the remaining part of the config to automate.
- **40 rules, one model, one run of each variant at temperature 0.**
- **The diagnosis row is after the fact:** it copies one field from the reference, so it isn't a result of any variant.

## Spend and data

$0.0316 over 201 calls through the same OpenRouter key and pinned host as the submitted system, logged in this experiment's own [spend.jsonl](spend.jsonl) (`outputs/spend.jsonl` and the README's Spend line are unchanged). The model saw only the policy documents' text: no receipts, labels or `rules.yaml` values.
