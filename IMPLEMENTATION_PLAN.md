# Implementation plan — Expense Policy Validator

Companion to [DECISIONS.md](DECISIONS.md). Each milestone ends in a **gate**: checks that pass or fail deterministically. No milestone starts until the one before it passes its gate and is committed.

## The strategy in four rules

1. **Build back to front.** The deterministic half (data → rules → engine → retriever → reporter) is built and proven first, using the *correct* item categories as input. It needs no API key and costs nothing. When the LLM stages arrive, the back half is already known to be right.
2. **Every stage has its own gate against the answer key or against invariants.** We never judge a stage only by the final disposition.
3. **Swap in the correct answer one stage at a time to assign blame.** Run the engine on the correct categories ("oracle mode"), then on predicted categories, then compare. Whatever changes between two runs is caused by the one stage that differs. This is what turns "it's not working" into "item RCP-012-L3 was misclassified".
4. **Freeze, cache, record.** Every LLM output is cached and tagged with model ID, provider, prompt version and policy version. A rerun makes zero API calls and produces byte-identical output, so a change in results always has a cause we can name.

### About using the answer key in oracle mode
The brief says the labels are "only to evaluate your system, never as input to it." Oracle runs are evaluation: an ablation that substitutes the correct answer for one stage to measure the others. They never feed the submitted results. Two guards keep this honest:
- **Code separation.** Gold labels load only in `evaluation/`. A test asserts that nothing under `validator/` imports from `evaluation/`, and that the input model the pipeline accepts has no `labels` or `notes` field.
- **No fitting to the key.** When oracle mode disagrees with the answer key, the fix must be justified by a sentence in the policy documents or the data dictionary. Record each one in the interpretation log (M4). Fixing a rule only because the label says so is overfitting to 28 receipts, and the interviewers will ask about this.

State both points in the README.

---

## Test data: what we test against

| Source | What it is | Used for | Never used for |
|---|---|---|---|
| Gold labels in `receipts.jsonl` (provided) | 32 receipts, 79 items; 28 / 65 in scope | M1 integrity checks, M4 oracle gate, M5 recall, M9 item metrics, M11 evaluation | prompts, few-shot examples, choosing the threshold |
| `notes` field (provided) | why each answer is correct, and the trap | analysing failures after a run | reading before writing prompts or rules |
| `dev/trap_items.jsonl` (we write it) | 20–30 synthetic items with expected categories | all prompt iteration; choosing the EXC-03 threshold | — |
| `tests/fixtures/synthetic_receipts.jsonl` (we write it) | 15–20 small synthetic receipts: engine edge cases and the 11 tier-2 rules | M4 unit tests | — |
| `review/translations.csv` (we fill it in) | hand check of every English gloss | M8 gate; translation blame in M10 | — |
| `review/item_attributes.csv` (we fill it in) | the recipient each line names, and its type, read from the line text | oracle mode (M4); recipient gate (M9) | — |

The gold set is the only labelled data, and it is also the test set, so every look at its errors risks tuning to it. Two rules limit that:
- All prompt work happens on the dev set.
- The pipeline is **frozen before its first real run** (M9). The numbers from that frozen run are reported as v1. Anything changed after seeing gold errors is reported as v2, next to v1.

**Caveat:** the engine is aligned with the gold answers in M4 oracle mode, before the freeze, under the policy-citation rule above. So v1 is blind for translation, the classifier and the threshold, but not for the engine. Say so in the README.

---

## Facts from the data that shape the plan

Checked against the delivered files in `data/`: 32 receipts, 79 items, 9 policies, 41 rules.

| Fact | Consequence |
|---|---|
| Of the 41 rules, 4 are out of scope (TIME-01 to 04), 4 are engine mechanics (EXC-01 to 04), and REG-05 and MCC-03 raise nothing on their own. That leaves **31 rules that raise exceptions or signals**. | **Tier 1** is the 20 that appear in the in-scope answers (19 exception rules plus MCC-02); the oracle gate covers them. **Tier 2** is the other 11 (MEAL-02, ENT-01, ENT-02, TECH-03, GIFT-03, HEALTH-01, DOC-02, DOC-04, REG-02, REG-03, REG-04). No gold receipt raises them, so synthetic receipts test them firing, and the oracle gate proves they don't fire wrongly on the 28. |
| MEAL-01, REG-02 and REG-03 add up meal spend per employee per calendar date, across receipts. HEALTH-01 adds up OTC spend across all of a trip's receipts. | The engine runs in two passes: build cross-receipt totals from all 32 classified receipts, then evaluate each receipt. All 32 go through the pipeline; 28 are scored. One receipt's misclassification can change another's result, so the blame report gets a cross-receipt check. |
| Gift rules depend on who a line is for (client, employee, government official) and whether it names them. That is only in the line text, not in the answer key. RCP-018 and RCP-022 are the two receipts with it. | The classifier also extracts a `recipient` (name and type) per line. Oracle mode needs recipients too, so we annotate them by hand from the line text in `review/item_attributes.csv`. |
| Every approval reference in the data is a `PA-` pre-approval. | Only TECH-02 can ever be waived. ENT-01 (VP approval), TECH-04 (manager approval) and the PERS-01/02 baggage-delay carve-out can never be satisfied. The four receipts with a reference (RCP-002, 011, 015, 016) are traps: the reference waives nothing they break. |
| Each item has a `language` field. 11 items are non-English, all in scope. | The normaliser doesn't detect language, and the translation hand review is 11 rows. |
| `amount_usd` and `total_amount_usd` are given, and equal amount × rate on every line (0 mismatches). | The engine computes USD itself, and a test asserts it equals the given value. TECH-02 uses unit price × rate. Domestic vs international needs a country → home currency map in `rules.yaml`. |
| One receipt is not itemized (RCP-023). | Its line is `other` by rule, set in code with no LLM call, and it never raises EXC-03. |
| HEALTH-01 is the only rule with two severities (low for OTC, medium for prescription). Every other rule has one, and the answer key agrees. | The M3 severity check allows a per-category severity for HEALTH-01 only. |
| EXC-04 lists 11 whole-receipt rules that cite no line items. Item rules never cite gratuity or service-fee lines, each rule appears at most once per receipt, and MCC-02 is always its own signal. | These become reporter gates in M6. |
| 19 of the 21 categories appear in the in-scope answers (no `gambling` or `airfare`). 13 have two items or fewer, and 7 have exactly one. | Macro F1 averages over 19 categories and is very noisy: one wrong item in a one-item category moves it by about 5 points. Show per-category counts next to it. |
| All 3 EMEA receipts are in EUR and at or below EUR 50. | REG-03 never needs a cross-currency euro rate, and REG-04 never fires in this data. Both still get synthetic tests. |
| The data dictionary defines matching exactly. An exception matches on receipt + `rule_id`, and its evidence is correct when the `line_ids` are equal (both empty for whole-receipt rules). Signals match on receipt + `rule_id`. A gold rule we never raise has recall 0 and precision n/a. | M11 implements these definitions word for word. Evidence correctness becomes a required metric, not a stretch goal. |

---

## Repo layout

```
validator/                 # the system: never imports evaluation/
  data.py                  # Pydantic models: ReceiptInput (no labels), LineItem
  taxonomy.py              # 21 categories + restricted flags
  policies.py              # parse the 9 docs → rule records {id, doc, text}
  normaliser.py            # deterministic cleanup + language tag
  translate.py             # cached LLM English gloss (normaliser stage 2)
  llm.py                   # OpenRouter client, disk cache, spend ledger
  classifier.py
  retriever.py
  engine/
    money.py               # Decimal, to_usd()
    context.py             # cross-receipt totals: meals per employee-day, OTC per trip
    rules.py               # one check function per rule ID
    engine.py              # overrides → checks → carve-outs → EXC-03
    disposition.py         # EXC-01, pure function
  reporter.py              # evidence + template explanations
  pipeline.py              # wires the five components
  baseline.py              # keyword matcher (swaps the classifier only)
evaluation/
  gold.py                  # loads labels; the ONLY place that reads them
  oracle.py                # engine on gold categories
  metrics.py               # F1, confusion matrix, Wilson intervals
  blame.py                 # per-mismatch stage attribution
  evaluate.py              # writes outputs/metrics.json + blame report
rules/rules.yaml
dev/trap_items.jsonl       # hand-written synthetic dev set (never from receipts)
outputs/                   # committed: results.jsonl, baseline.jsonl, metrics.json, oracle.jsonl
outputs/v1/                # committed: the frozen first blind run (results + metrics)
cache/                     # committed: LLM responses (no secrets)
review/translations.csv    # hand-review sign-off for glosses
review/item_attributes.csv # hand-annotated recipient name and type per line (oracle input)
docs/aws-design.md
app.py                     # Streamlit viewer
tests/                     # fixtures/synthetic_receipts.jsonl: hand-written engine edge cases
NOTES.md                   # running log: interpretation decisions + AI mistakes caught
```

**Commands** (Makefile targets, each a one-liner):

| Command | Does | Needs API key |
|---|---|---|
| `make test` | all offline tests (`pytest -m "not llm"`) | no |
| `make oracle` | engine on gold categories, prints the match table | no |
| `make smoke` | LLM capability checks (M7) | yes |
| `make run` | full pipeline; uses the cache | only on a cache miss |
| `make eval` | metrics + blame report → `outputs/metrics.json` | no |
| `make baseline` | keyword baseline through the same engine | no |
| `make app` | `streamlit run app.py` | no |

---

## Milestones at a glance

| # | Milestone | Gate in one line | Time | Key? |
|---|---|---|---|---|
| M0 | Setup | tests run, `.env` ignored, first commit | 15 min | no |
| M1 | Data layer | counts and cross-references match the brief exactly | 25 min | no |
| M2 | Policy corpus | exactly 41 rules; doc for every gold exception matches | 20 min | no |
| M3 | `rules.yaml` | every in-scope rule encoded; numbers and evidence found in the source text | 30 min | no |
| M4 | Policy engine | unit + synthetic-receipt tests pass; **oracle mode matches the answer key on all 28** | 90 min | no |
| M5 | Retriever | retrieval recall 100%; engine output unchanged vs "all rules" | 20 min | no |
| M6 | Reporter + output schema | every result validates; every evidence string is in its doc | 20 min | no |
| ✅ | **Checkpoint: the back half is proven at $0** | | | |
| M7 | LLM client + smoke test | the four unverified capabilities confirmed or fallback chosen | 20 min | yes |
| M8 | Normaliser + translation | deterministic tests pass; 100% gloss coverage; hand review signed off | 15 min | yes |
| M9 | Classifier | dev-set gate; **v1 frozen and tagged**; then item metrics vs gold | 45 min | yes |
| M10 | Integration + blame | rerun is byte-identical with 0 calls; every mismatch has a named stage | 25 min | cache |
| M11 | Evaluation | metric code passes hand-computed tests; all 7 Part 3 items produced | 40 min | no |
| M12 | Keyword baseline | frozen word list; runs through the same engine; deterministic | 20 min | no |
| M13 | Viewer | Streamlit `AppTest` renders all three pages; mismatch count = metrics | 25 min | no |
| M14 | AWS design page | five topics covered; cost uses tokens measured in M10 | 45 min | no |
| M15 | README + notes | section checklist; spend = ledger total; ≤ 2 pages | 30 min | no |

That's about 8 hours. M0–M6 can be finished before the OpenRouter key arrives.

---

## M0 · Setup (15 min)

**Build:** `git init`, Python 3.12, `pyproject.toml` with pinned dependencies (`openai`, `pydantic`, `pyyaml`, `pandas`, `scikit-learn`, `statsmodels`, `streamlit`, `pytest`), `.gitignore` (`.env`, `.venv/`), `.env.example` with `OPENROUTER_API_KEY=`, Makefile skeleton, `pytest.ini` with an `llm` marker, empty `NOTES.md`.

**Gate:**
- [ ] `make test` exits 0.
- [ ] `git check-ignore .env` prints `.env`.
- [ ] First commit exists.

## M1 · Data layer (25 min)

**Before coding:** read the data dictionary (`data/README.md`) end to end. Write each convention it settles (who the attendee count includes, which field holds the approval reference, how outputs are compared) into `NOTES.md` as a one-line rule. Where it contradicts DECISIONS.md, the data dictionary wins.

**Build:** Pydantic models. `ReceiptInput` carries everything the pipeline may see; gold labels are parsed separately in `evaluation/gold.py`. An in-scope filter excludes RCP-020, 021, 029 and 030.

**Gate** (`tests/test_data.py`):
- [ ] 32 receipts, 79 line items; in scope: 28 receipts, 65 items.
- [ ] In-scope gold dispositions: 9 pass, 16 flag, 3 review.
- [ ] All `line_id`s unique; every `line_ids` entry in an expected exception exists in that receipt.
- [ ] Taxonomy parses to exactly 21 categories; every gold category is one of them; every gold `restricted` flag equals the taxonomy's flag for that category.
- [ ] On every line, `amount = quantity × unit_price` and `amount_usd = amount × fx_rate_to_usd`; line amounts sum to `total_amount`. All three hold in the delivered data.
- [ ] Every `employee_country` maps to the `entity` the data dictionary gives (APAC = IN, JP, SG; EMEA = GB, DE, FR, NL, IE, PT, ES; otherwise GLOBAL).
- [ ] `ReceiptInput` has no `labels` or `notes` field; nothing under `validator/` imports `evaluation/` (checked by scanning the import statements).
- [ ] One test per data-dictionary convention from `NOTES.md` where it can be expressed as a check.

**If it fails:** the loader or our reading of the data is wrong. Fix it before anything else, because every later gate trusts these counts.

## M2 · Policy corpus (20 min)

**Build:** `policies.py` splits each of the 9 documents into rule records `{id, doc, heading, text}` and distinguishes where a rule is *defined* from where it is merely *referenced* in another document.

**Gate** (`tests/test_policies.py`):
- [ ] Exactly 41 rules are defined, as the data dictionary says. Definitions look like `**MEAL-01 — Daily meal cap.**`, so a simple regex finds them.
- [ ] The set of defined rule IDs equals the set an independent regex scan finds at definition sites.
- [ ] Each rule ID is defined in exactly one document.
- [ ] Every `rule_id` in the gold expected exceptions and review signals is a parsed rule, and its defining doc equals the gold `policy_doc`.
- [ ] No rule text is empty.

**Also:** start a "questions for the panel" list as you read. Send it early; they answer within one working day.

## M3 · `rules.yaml` (30 min)

**Build:** one entry per in-scope rule: `id`, `doc`, `severity`, thresholds and other params, `applies_to` (categories, MCCs, expense types, regions), `overrides`, `requires_evidence` (with the reference kind it needs), `scope` (receipt or items), `tier` (1 or 2), `kind` (exception / review_signal / mechanic), and the verbatim `text`. Add top-level tables for the MCC risk tiers, the DOC-03 expense-type coverage, region membership, and country → home currency. Hand-write it, or have an LLM draft it from the documents, then review every line. TIME-01 to TIME-04 go in an explicit `out_of_scope` list.

**Gate** (`tests/test_rules_yaml.py`):
- [ ] The YAML validates against a Pydantic schema.
- [ ] Every rule ID from M2 is either in the YAML or in `out_of_scope`. Nothing is silently missing.
- [ ] Every `text` is an exact substring of its doc (after whitespace normalisation).
- [ ] **Every numeric param appears in that rule's text** (e.g. `200` in the equipment rule). This catches a number an LLM invented while drafting.
- [ ] Every `overrides` target exists.
- [ ] Each rule has one severity, except HEALTH-01, which has one per category (low for OTC, medium for prescription). For each gold expected exception, the YAML severity equals the gold severity.
- [ ] Exactly the 11 whole-receipt rules listed in EXC-04 have `scope: receipt`.
- [ ] The tier-1 set equals the set of rule IDs in the in-scope answers.

**If you used an LLM to draft it:** record what it got wrong and which check caught it in `NOTES.md`. That's ready-made material for the README's AI collaboration section.

## M4 · Policy engine (60 min), the most important gate

**Build:**
- `money.to_usd()` with `Decimal` and the receipt's rate.
- `context.py`, which first builds the cross-receipt totals (meal spend per employee per date, OTC spend per trip) from all classified receipts.
- One check function per rule, tier 1 before tier 2.
- Override resolution, then independent evaluation of each rule.
- Explicit carve-out outcomes (`waived_by`), with a reference-kind check: `PA-` is a pre-approval, which satisfies TECH-02 only.
- `disposition.py` (EXC-01).
- EXC-03 review signals for items below the confidence threshold. Those items are left out of every other rule's calculation.

**Gate A, unit tests** (`tests/test_engine.py`). Build the inputs by hand in the tests, not from the classifier:
- [ ] The five brief cases: RCP-027 (carve-out with no evidence), RCP-016 (APAC override), RCP-006 + RCP-026 (threshold boundary), RCP-031 (one low-severity exception → review), RCP-028 (review signal only → review).
- [ ] An EXC-01 truth table over every combination of exception severities and review signals.
- [ ] Threshold exactly at the cap (is the cap inclusive? the policy text decides; cite it in the test).
- [ ] Currency conversion: a known foreign amount converts to the expected USD with correct rounding.
- [ ] Determinism: same input twice gives identical output; shuffled line-item order gives identical output.
- [ ] An item below the confidence threshold produces an EXC-03 review signal and no exception, and is left out of every other rule's totals (e.g. the meal cap). A non-itemized line never raises EXC-03.
- [ ] A `PA-` reference waives TECH-02 and nothing else. Test it against ENT-01, TECH-04, PERS-02 and ALC-03.
- [ ] Cross-receipt totals: two meal receipts by one employee on one date are added together for the cap, and MEAL-01 is raised on both. OTC spend is added across all of a trip's receipts.
- [ ] **Synthetic receipts:** 15–20 small ones in `tests/fixtures/synthetic_receipts.jsonl`. An assistant can draft them, but check every expected result against its policy sentence by hand. Each has its expected result and the policy sentence that decides it:
  - an amount exactly at a cap, and one cent over
  - a foreign-currency amount that crosses a cap only after conversion
  - a regional override, with and without the region
  - a carve-out with its evidence recorded, and without it
  - a mix of severities that exercises the EXC-01 ladder
  - one receipt where each of the 11 tier-2 rules fires

  These cover edge cases and rules the 28 gold receipts don't, and they can't have been overfit. The oracle gate below already proves no tier-2 rule fires wrongly on the 28.

**Before Gate B:** fill in `review/item_attributes.csv` from the line text: for each line that names a recipient, the name and type (client or prospect, employee, government official). Only RCP-018 and RCP-022 have one.

**Gate B, oracle mode** (`make oracle`). Run the engine on the **gold** categories with confidence 1.0, the hand-annotated recipients, and **all rules** enabled, so it is isolated from both the classifier and the retriever. Run all 32 receipts so the cross-receipt totals are complete, and score the 28:
- [ ] Disposition matches gold on 28 / 28.
- [ ] Exception `rule_id` set matches gold on every receipt.
- [ ] `line_ids` match exactly on every matched exception.
- [ ] Review-signal `rule_id`s match gold on every receipt.
- [ ] Write the result to `outputs/oracle.jsonl` and commit it as a snapshot. Later changes that alter it should fail a regression test.

**If it fails:** this is rule logic and nothing else, because the inputs are perfect. Find the policy sentence that explains the gold answer, fix the rule, and record it in `NOTES.md` as an interpretation decision. If no sentence explains it, don't fit the code to the label: leave the mismatch, report it, and add it to the questions for the panel.

## M5 · Retriever (20 min)

**Build:** a structured index from `applies_to`, regex expansion of cross-references, and always-on rules. It logs the rule IDs it returns for every receipt.

**Gate** (`tests/test_retriever.py`, using gold categories):
- [ ] **Recall = 100%:** for every receipt, retrieved rules ⊇ the gold exception and review-signal rule IDs.
- [ ] Engine(gold categories, retrieved rules) == engine(gold categories, all rules) on all 28. This proves retrieval drops nothing the engine needs.
- [ ] Report the average number of rules retrieved per receipt, for information only.

**If it fails:** `applies_to` is missing a category, MCC or region. Fix the YAML, not the engine.

## M6 · Reporter + output schema (20 min)

**Build:** a JSON Schema for the brief's output shape (extra fields allowed; the brief's fields required with the exact names). The reporter fills `evidence` from the stored rule text and `explanation` from templates using the computed numbers.

**Gate** (`tests/test_reporter.py`):
- [ ] Every oracle-mode result validates against the schema.
- [ ] Every `evidence` string is an exact substring of its `policy_doc`.
- [ ] Every `line_id` in an exception belongs to that receipt.
- [ ] Every threshold explanation contains the computed amount and the cap.
- [ ] The 11 whole-receipt rules cite no line items; item rules never cite a `service_fee` line.
- [ ] No rule appears twice on one receipt. MCC-02 appears as a review signal whenever the MCC is medium risk, even alongside exceptions.

### ✅ Checkpoint
Rules, engine, retriever and reporter are now proven against the answer key, with $0 spent. From here on, any new mismatch comes from translation, classification or the confidence threshold.

## M7 · LLM client + smoke test (20 min)

**Build:** `llm.py`: the OpenAI SDK pointed at OpenRouter; provider pinning (`order`, `allow_fallbacks: false`, `require_parameters: true`); a disk cache keyed on (model, provider, prompt version, input hash); and a spend ledger that writes tokens and dollars from each response's `usage` to `outputs/spend.jsonl`. Retries with backoff.

**Gate** (`make smoke`, tests marked `llm`). These confirm the "Still unverified" items in DECISIONS.md:
- [ ] Strict `json_schema` is enforced: the response parses and `category` is in the enum.
- [ ] Logprobs come back and can be lined up with the category tokens, giving a probability between 0 and 1.
- [ ] Temperature 0 is accepted, and reasoning off is honoured (no reasoning tokens in `usage`).
- [ ] The response's `provider` equals the pinned provider.
- [ ] A second identical call is a cache hit: zero new ledger entries.
- [ ] The total smoke-test spend in the ledger is under $0.01.

**If any fail:** take the fallback from DECISIONS.md (verbalised confidence, or Haiku 5.5) and write down which and why in DECISIONS.md. This is a decision point, not a bug.

## M8 · Normaliser + translation (15 min)

**Build:** deterministic `normaliser.py` (NFKC, whitespace, SKU and quantity noise; the language comes from the data's `language` field), then `translate.py`, one cached LLM call per receipt that returns an English gloss for each non-English item.

**Gate:**
- [ ] Unit tests on hand-written strings for each cleanup rule, including Japanese full-width characters.
- [ ] 100% of non-English items have a non-empty gloss; English items skip the call and pass through unchanged.
- [ ] **Hand review:** `review/translations.csv` lists each line ID, original, gloss and your `ok` column (11 rows), all filled in and committed. Translation is the one stage with no gold answer, so the recorded human check is its gate. It also covers "what I checked by hand" in the README.

## M9 · Classifier (45 min)

**Build first: the dev set.** In `dev/trap_items.jsonl`, write 20–30 synthetic items with expected categories: misleading names (dishes named after drinks, desserts with spirits, cooking wine at a grocery store), all six languages, items from merchants that suggest the wrong category, and lines that name a recipient (a client, a colleague, a public official). Write them yourself; never copy or paraphrase receipts or notes.

**Build:** `classifier.py`: one call per receipt, strict schema, `category` before any reason field, confidence from logprobs (or the fallback), and `restricted` looked up from the taxonomy in code.
- It also returns a `recipient` per line: a name, and a type of client or prospect, employee, government official, or none.
- And an `item_kind` for the three things the policies name that have no category, which the taxonomy says to class as `other`: `entertainment_venue` (ENT-01), `mobile_phone` (TECH-03), `prescription_medication` (HEALTH-01), or none. The engine's input model (`validator/models.py`) already has the field.
- Non-itemized lines are set to `other` in code, with no call.
- The `category` enum is exactly the 21 taxonomy categories. The taxonomy file invites new ones; the brief says not to.

**Gate, dev set (before touching the real receipts):**
- [ ] 100% schema-valid; dev-set accuracy recorded.
- [ ] Choose the EXC-03 threshold from the dev set's confidence-vs-correct plot. Write it into the config and **commit it before the first real run**, so the git history shows the threshold wasn't tuned on the answer key.

**Freeze v1 (before the first real run):**
- [ ] Commit the prompt, `prompt_version`, threshold, `rules.yaml` and model/provider config, and tag the commit `v1-frozen`.
- [ ] The first full run on the real receipts (M9 → M11) writes to `outputs/v1/` and is committed untouched. Those are the blind numbers.
- [ ] From here on, every change made after seeing a gold error is logged in `NOTES.md` (which error, what changed, why) and bumps the version.

**Gate, real receipts:**
- [ ] All 79 items classified and schema-valid.
- [ ] `restricted == taxonomy[category].restricted` for every item.
- [ ] Extracted recipients match `review/item_attributes.csv` on every line. Both lines on RCP-022 must come back as a government official.
- [ ] Every dev-set item with an item kind gets it back, and no gold item gets one (the data contains none).
- [ ] A rerun gives identical output with zero API calls.
- [ ] Optional: one rerun with the cache off measures the answer-flip rate. Report it; it costs pennies.
- [ ] Item-level macro F1 and restricted F1 against gold on the 65 in-scope items, plus a list of every misclassified item. **Look at every restricted-flag error before integrating.** Those are the errors that create false flags or missed violations.
- [ ] Merchant ablation: rerun the trap items without merchant or MCC. If accuracy goes *up*, the context is biasing the model.

## M10 · Integration + blame report (25 min)

**Build:** `pipeline.py` runs normalise → translate → classify → retrieve → engine → report for all 32 receipts and writes `outputs/results.jsonl`. Every record carries model ID, provider, prompt version, policy version (a hash of `rules.yaml`) and threshold.

**Gate:**
- [ ] Every record validates against the M6 schema.
- [ ] `make run` twice gives byte-identical `results.jsonl` with zero new ledger entries.
- [ ] **The blame report gives a named stage for every mismatched receipt; "unknown" count = 0.**

**Blame rules** (`evaluation/blame.py`). For each receipt where the pipeline disagrees with gold, check in this order:

| Check | If true → blame |
|---|---|
| Oracle mode (M4) also disagrees on this receipt | **rule logic** (known, already logged) |
| An item involved in a gold or spurious exception is misclassified, and its gloss was marked wrong in the M8 review | **translation** |
| An item involved in a gold or spurious exception is misclassified | **classification** |
| An extracted recipient differs from `review/item_attributes.csv` | **recipient extraction** |
| An item on *another* receipt in the same employee-day or trip group is misclassified | **classification (cross-receipt)** |
| Retrieval on *predicted* categories misses a gold rule | **retrieval** (via classification) |
| An EXC-03 signal changed the disposition | **confidence threshold** |
| None of the above | **unknown**: investigate; the gate fails |

Each check substitutes the correct answer for one stage, so the stage that differs is the cause. Part 3's "two failure cases" come straight out of this table.

## M11 · Evaluation (40 min)

**Build:** `evaluate.py` produces every Part 3 item and writes `outputs/metrics.json`. It covers:
- macro F1 (`labels=sorted(set(y_true))`, `zero_division=0`) over the 19 categories in the in-scope answers, with per-category counts beside it, and restricted F1
- the 3×3 confusion matrix
- false positives out of 9 and missed violations out of 16
- Wilson 95% intervals
- the baseline comparison
- the blame table
- a threshold sweep, labelled as after-the-fact analysis
- evidence correctness and per-rule precision and recall, using the data dictionary's matching rules exactly. An exception matches on receipt + `rule_id`, and its evidence is correct when the `line_ids` are exactly equal. A gold rule we never raise has recall 0 and precision n/a.
- v1 (frozen, blind) and final numbers side by side, with the change log from `NOTES.md`

**Gate** (`tests/test_metrics.py`, on tiny hand-computed examples):
- [ ] A four-item toy case where you worked out macro F1 by hand matches the code.
- [ ] A gold category the model never predicts contributes F1 = 0; a category that is only predicted doesn't enter the average.
- [ ] The confusion matrix sums to 28, with gold row sums 9 / 16 / 3.
- [ ] The Wilson interval for 1/9 is about [0.02, 0.43].
- [ ] A matching toy case: an exception with the right `rule_id` but wrong line IDs counts as matched but evidence-incorrect.
- [ ] Running `make eval` on oracle outputs gives perfect disposition scores. This checks the metric code against a known answer.

## M12 · Keyword baseline (20 min)

**Build:** a word list per category with restricted terms in all six languages, matched on whole words (substring for Japanese) after casefold, NFKC and accent stripping. Matched items get confidence 1.0. The labels go through the **same** retriever, engine and reporter.

**Gate:**
- [ ] The word list is committed **before** the first baseline run (check the git history).
- [ ] Unit tests on synthetic strings: "gin" doesn't match "ginger"; "rum" doesn't match "drum"; plurals match.
- [ ] Deterministic: two runs give an identical `baseline.jsonl`.
- [ ] Baseline numbers appear in `metrics.json` next to the system's.

## M13 · Viewer (25 min)

**Build:** `app.py` with three pages (receipt list, receipt detail, metrics) via `st.navigation`, reading only the committed outputs.

**Gate** (`tests/test_app.py`, using `streamlit.testing.v1.AppTest`):
- [ ] Each page runs with no exception.
- [ ] The list shows all 32 receipts; the number of highlighted mismatches equals the mismatch count in `metrics.json`.
- [ ] `make app` launches it.

## M14 · AWS design page (45 min)

**Build:** `docs/aws-design.md`, one page, following section 10 of DECISIONS.md.

**Gate:**
- [ ] Covers all five topics: architecture, policy documents, authentication and secrets, cost, before production.
- [ ] The cost table uses the **average tokens per receipt measured in the M10 ledger**, not an assumption, and shows the arithmetic.
- [ ] Fits on one page.

## M15 · README + AI collaboration notes (30 min)

**Gate** (a checklist):
- [ ] Sections: Architecture · Prompts · Policy engine (with the threshold number) · Evaluation summary · AI collaboration · Spend · What I would do next.
- [ ] Lists the services that received the data (assistants, OpenRouter, the pinned upstream provider).
- [ ] Walks one receipt through end to end.
- [ ] The Spend line equals the `spend.jsonl` total (and roughly matches the OpenRouter dashboard); the model ID and the reason for choosing it are given.
- [ ] Explains the oracle-mode guards.
- [ ] Reports v1 (blind) and final numbers side by side, and says the engine was aligned in oracle mode before the freeze.
- [ ] Setup steps and environment variables are given.
- [ ] At most 2 pages.
- [ ] AI collaboration notes come from `NOTES.md`. Each gate failure an assistant caused is a concrete example of "what it got wrong and how I caught it".

---

## Working rules

- **One commit per milestone,** after its gate passes, e.g. `M4: policy engine; oracle 28/28`. That gives the git history the brief asks for.
- **`NOTES.md` is written as you go,** not at the end: interpretation decisions, questions for the panel, and every AI mistake with the check that caught it.
- **Changing a prompt bumps `prompt_version`,** which misses the cache on purpose. Rerun M9's gates and diff the two result files before keeping the change.
- **After the v1 freeze, nothing changes silently.** Every change prompted by a gold error goes in `NOTES.md` and shows up as v2.
- **Watch the ledger.** If total spend passes $1, stop and find out why. A full run should cost cents.

## If you fall behind, cut in this order

1. The Jev side experiment, the merchant ablation and the cache-off flip-rate rerun (M9 optional items)
2. Stretch metrics in M11 (per-rule precision and recall, line-ID match)
3. Viewer polish (keep the three pages and the `AppTest`)
4. Tier-2 rules (no gold receipt raises them). Build them last; if time runs out, list the missing ones in the README.
5. The LLM-drafted YAML (hand-write it instead)

**Never cut:** the M4 oracle gate, the M10 blame report, or the M11 metrics. Those are what let you say *why* the system gets something wrong.
