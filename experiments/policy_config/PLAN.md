# Experiment 3: an LLM pipeline from policy text to the standardized config (plan)

Written before the experiment's first model call. The submitted system, `rules/rules.yaml` and every committed output stay unchanged.

## Question

`rules/rules.yaml` was drafted once by an AI assistant and reviewed by hand. Can a repeatable pipeline (split the policies into rules, have an LLM fill the config schema for each one, check the result) produce a config that the unchanged engine can run, and that reaches the same decisions? Where does it go wrong, and can it tell when it's unsure?

## What stays fixed

- **The rules to fill:** the 40 encoded rule IDs. TIME-04 stays out of scope; its trip records have no dates.
- **The shared tables** (regions, home currencies, euro rate, merchant-code risk tiers, on-premises codes, meal expense types, the DOC-03 table, reference prefixes) are copied from `rules.yaml`. This experiment is about per-rule fields; the tables are a later step.
- **`id` and `doc`** come from the parser (`validator/policies.py`), not the model.
- **`tier`** is copied from `rules.yaml` and never scored. It isn't in the policy text: it marks which rules the answer key uses, and the engine never reads it.

## What the model fills, per rule

`kind`, `severity`, `scope`, `params` (values), `applies_to`, `overrides`, `line_overrides`, `replaces`, `requires_reference` and `evidence` (a verbatim quote). These are the fields the engine and retriever use.

**What the model is given:**
- the field definitions and their allowed values (the 21 categories, item kinds, recipient types, expense types, regions, risk tiers, reference kinds);
- one made-up example rule, not from these policies;
- the rule's own text, title, document and section;
- **the names of the parameters the rule's Python check reads** (for example `domestic_cap_usd`), but never their values. The names are the code's interface; the values must come from the text.

**Never given:** `rules.yaml`'s values, the answer key or any receipt. A test checks that the prompts contain no value from `rules.yaml` beyond what the policy text itself says.

## Variants

1. **Plain:** the rule's own text only. Temperature 0.
2. **Retrieval:** also the full text of every rule it cites and every rule that cites it, along the citation graph the parser already builds. These are the links overrides and replacements depend on, such as REG-02 and MEAL-01. Temperature 0.
3. **Self-consistency:** variant 2 run three times at temperature 1, with the majority value per field. A field where the three runs disagree is flagged for human review.

**Model:** DeepSeek V4.1 Flash through OpenRouter, the submitted system's own model and host pinning, with JSON output validated against the schema and one retry. Calls are cached, and spend goes to this experiment's own `spend.jsonl`, never `outputs/spend.jsonl`.

## Measures

1. **Field accuracy** against the hand-reviewed `rules.yaml`, field by field and per rule (every scored field right). `evidence` counts as right when it is verbatim policy text, because more than one sentence can be a fair quote.
2. **The existing config tests** (`tests/test_rules_config.py`) run on each generated config: verbatim quotes, numbers and severities found in the text, the whole-receipt list matching EXC-04, overrides pointing at real rules, the review-signal set. The one test that reads the answer key (severity against the gold exceptions) is reported separately.
3. **End to end,** with the unchanged retriever and engine:
   - **Oracle mode** (the answer key's categories): exactly-right receipts out of 28, as hand `rules.yaml` gets 28 / 28.
   - **System mode** (the committed classifications): exactly-right receipts out of 28.
   - **CORD:** how many of the 100 decisions match those made with hand `rules.yaml`.
   - If a config crashes the engine, the error is reported. A second, "lenient" run then marks rules with no Python check as mechanics, so the decisions can still be compared; the repair is reported.
4. **Self-consistency as a flag:** of the fields the majority got wrong, how many were flagged, and of the flagged fields, how many were wrong.
5. **Cost and tokens.**

## What would count as it working

A variant counts as reproducing the config if, run strict, it gets 28 / 28 in both oracle and system mode, passes every policy-text config test, and matches all 100 CORD decisions. A variant that gets the decisions right with some fields different counts as "works, but differs", with each difference listed. Self-consistency is useful if most wrong fields are flagged while few right ones are.

## Limits, stated in advance

- **The reference was itself LLM-drafted,** then reviewed, and partly checked against the answer key (severities). "Matches `rules.yaml`" means "matches the reviewed version", not "matches the truth".
- **The model knows the parameter names,** which hint at what each rule needs. Without them a generated config couldn't run the existing checks at all; a pipeline that also wrote the checks would be a further experiment.
- **Some fields are design conventions as much as policy,** for example which rule `replaces` which, or whether a rule applies `always`. A mismatch there may be a defensible alternative reading, not an error; the per-rule diffs are listed so each can be judged.
- **40 rules** is a small sample, and one model.
