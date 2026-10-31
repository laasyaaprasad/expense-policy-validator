# Working notes

Written as the work happens, not at the end. The README's AI collaboration section is drawn from here.

## Data conventions

One line per convention the data dictionary (`data/README.md`) settles. Each gets a test in M1 where it can be checked; the test name is in brackets.

1. `attendee_count` includes the employee; `null` means no count was recorded.
2. `entity` derives from `employee_country`: APAC = IN, JP, SG; EMEA = GB, DE, FR, NL, IE, PT, ES; everyone else GLOBAL. [`test_entity_matches_country`]
3. `transaction_datetime` is local time at the merchant, with no time zone.
4. Multiply an amount by `fx_rate_to_usd` to get USD. [`test_usd_amounts_match_rate`]
5. `pre_approval_ref` is the one field for any approval or exception reference, and every reference in the data is a pre-approval written `PA-<year>-<number>`. [`test_every_reference_is_a_pre_approval`]
6. Trip records hold no start or end dates, so TIME-04 can never apply.
7. The data holds no VAT number and no card type. A missing VAT number counts as missing (REG-04); no rule uses the card type.
8. Whether a recipient is a government official, a client or an employee is stated only in the line text.
9. Each rule appears at most once per receipt; one exception lists every item that breaks it. [`test_each_rule_at_most_once_per_receipt`]
10. The 11 whole-receipt rules (MCC-01, MEAL-01, MEAL-02, DOC-01 to 04, REG-02 to 04, TIME-03) have empty `line_ids`. Every other exception lists its items, never a gratuity or service fee. [`test_line_ids_follow_scope_convention`]
11. MCC-02 is always its own review signal, even on a receipt that also has an exception. (Needs the MCC risk table; tested in M6.)
12. No answer uses EXC-03, so any EXC-03 signal we raise is unmatched, and so is any disposition change it causes.
13. A non-itemized charge is `other`, not uncertain. [`test_non_itemized_lines_are_other`]
14. HEALTH-01 is the only rule with two severities: low for OTC, medium for prescription. (Tested in M3.)
15. Matching: an exception matches on receipt + `rule_id`; its evidence is correct when `line_ids` are exactly equal (both empty for whole-receipt rules); signals match on receipt + `rule_id`; a gold rule never raised has recall 0 and precision n/a; a macro average is the mean of the per-category scores. (Implemented in M11.)

## Interpretation decisions

Each time the engine is changed to agree with the answer key, the policy sentence that justifies it.

| Receipt | Rule | Decision | Policy sentence |
|---|---|---|---|

Oracle mode matched the answer key on 28 / 28 on its first run (M4), so no rule was changed to agree with a label. The choices below were made before that run, where the policy text leaves room. Each is covered by a test.

| Where | Rule | Decision | Policy sentence |
|---|---|---|---|
| all | EXC-03 | Uncertain lines are left out of receipt totals too (MEAL-02, REG-04), not only out of category tests. | "leave that item out of every other rule's calculation for the receipt" |
| all | EXC-04 | A rule that cannot be applied (no conversion rate, date or total) is reported under `not_evaluated`. It does not pass silently, and no exception is raised. | "An exception that cannot name its evidence must not be raised." |
| all | thresholds | Caps are inclusive: exactly $75, $200, $30 per attendee, $150 or EUR 50 does not breach. | "Spend above the cap", "up to $200 per item", "above $150", "above EUR 50" |
| ALC-01 | ALC-01 | Client Entertainment with an attendee count of 1 still breaks ALC-01. | "records an attendee count of two or more" |
| GIFT-01 | GIFT-01 | Applies to gifts for clients, for unnamed recipients, and for named recipients of unknown type. Gifts for employees go to GIFT-03; gifts for officials go to GIFT-04. | "Gifts to clients and prospects are capped at $50 per recipient per occasion." |
| GIFT-01 | GIFT-01 | A recipient is read only from the line's own text. No other field on the record could name one. | "in the line description or elsewhere on the expense" |
| GIFT-01, GIFT-03 | gifts | Gift cards are left out of both. Food and drink on a meal receipt, and service fees, are never gifts. | "Gift cards and other cash equivalents fall under GIFT-02 alone"; "Food, drink and gratuities consumed at a meal are not gifts" |
| HEALTH-01 | HEALTH-01 | Every `health_otc` line counts as OTC medication, lozenges and first aid included. When OTC and prescription lines both break the rule on one receipt, one medium exception cites all of them. | "Each rule appears at most once per receipt." |
| PERS-01 | PERS-01 | The baggage-delay carve-out covers every `personal_care` line, and the 24-hour delay is taken as given by the reference. No field records either. | "essential toiletries are reimbursable up to $150, provided the delay reference is recorded" |
| REG-03, REG-04 | regional | A non-euro receipt is converted at a euro rate taken from a euro receipt on the same date. If there is none, the rule is `not_evaluated`. All EMEA receipts in the data are in euros. | "converted at the euro rate on the transaction date" |
| DOC-04 | DOC-04 | A missing currency also raises DOC-04, because the receipt cannot be shown to be in USD. | "must record the transaction currency and the conversion rate used" |
| RCP-020, RCP-021 | MEAL-01 | MEAL-01 is raised on the split pair. TIME-03 is out of scope; gold has TIME-03, which replaces the cap. These receipts are not scored. | "It replaces the threshold rule being split around" |

## EXC-03 threshold rule (written before the dev set was first run)

The rule is fixed here before any classification output has been seen, on the dev set or the receipts. Each candidate threshold t in {0.3, 0.4, …, 0.9} scores the number of wrong dev items with confidence below t, minus the number of right dev items below t. The best score wins, and a tie goes to the lower t (fewer signals). If no candidate scores above zero, the threshold stays at 0.5, the default argued in DECISIONS.md section 3. The real receipts are never used to choose or adjust it. Their threshold sweep is reported only as analysis after the v1 freeze.

**Result (M9).** The 43 dev items came back right on category, item kind and recipient in all three runs (as configured, a fresh repeat, and without merchant context). None flipped, and the lowest confidence was 0.96, so every candidate scored 0 and the threshold stayed at 0.5 (`outputs/dev/threshold.json`). The dev set is too easy to tell thresholds apart. That says nothing about whether 0.5 is a good threshold.

## The v1 run (M9)

The config was frozen at tag `v1-frozen` (`outputs/v1/frozen_config.json`). The first classification of the real receipts was committed untouched (`outputs/v1/classifications.jsonl`, commit 8f9cc38) before anything was scored against gold. That took 31 calls, all valid on the first attempt, for $0.0074.

- **65 / 65 in-scope items right.** Macro F1 is 1.000 over the 19 answer categories, restricted F1 is 1.000, and there are no restricted-flag errors (`make items`). The three named recipients match `review/item_attributes.csv`, and no line got an item kind.
- **EXC-03 never fires.** The lowest confidence on any of the 79 items is 0.979 (the iPad line in Japanese). With the threshold at 0.5, no item is uncertain, so the confidence signal plays no part in v1's dispositions.
- **Stable, and not leaning on the merchant.** A fresh repeat of all 31 calls at temperature 1 changed 0 of 79 answers. A run without merchant and expense type also got 65 / 65 and changed nothing. The two runs cost $0.0150 (`make items` reports them from `outputs/v1/classifications_{repeat,no_context}.jsonl`).
- **Why it is so clean.** The traps in the data are the examples the taxonomy and the meals policy give themselves ("Beer-battered cod", "Arnold Palmer", "virgin mojito", "Cooking wine", rum cake, barware). The prompt quotes none of them, but its definitions follow the taxonomy table. A capable model given those documents is expected to get them right.
- **What "blind" means here.** No prompt, setting or threshold was changed after a look at gold. The prompt and dev set were still written by people who had read the receipt lines and the aggregate label counts while planning (IMPLEMENTATION_PLAN.md, "Facts from the data"). So v1 is not a test of a prompt written without knowledge of the data. With 65 items and 7 one-item categories, a perfect score is also weak evidence of how the classifier would do on new receipts.

**Integrated (M10).** The full pipeline (`make run`) matches the answer key on 28 / 28 in-scope receipts, the same as oracle mode. Over all 32 it gives 18 flag, 3 review and 11 pass. The four out-of-scope receipts differ only where TIME rules apply. So the blame report has nothing to blame on v1. Its tests plant one fault per stage in the v1 classifications and check that the right stage is named, including a case it must call "unknown".

**Evaluated (M11, `make eval`).** False positives are 0 / 9 (95% Wilson interval 0–30%) and missed violations are 0 / 16 (0–19%). All 26 gold exceptions are found, none extra, and each cites exactly the gold line IDs. The intervals are the honest part: with 9 should-pass receipts, a perfect score is consistent with a false-positive rate as high as 30%. The after-the-fact threshold sweep changes nothing up to 0.95. At 0.98 the iPad line becomes uncertain and one receipt goes wrong; at 0.999 five lines do and four receipts go wrong. On this data the threshold could sit anywhere below 0.95 without changing a result.

## Keyword baseline (M12)

The word list (`rules/keywords.yaml`) was committed in f3ea89b, before the baseline's first run on the receipts. It has no brand names. The assistant that wrote it had seen the receipt lines (printed for the M9 leakage check), and two entries may owe something to that: `ipa`, and the German compound `weißwein`, the only compound in the list. So its score is, if anything, optimistic.

Through the same retriever and engine, the baseline gets 49 / 65 items and 20 / 28 receipts exactly right (system: 65 and 28). Restricted F1 is 0.791 and macro F1 0.738. False positives are 2 / 9 and missed violations 2 / 16 (system: 0 and 0). Its 16 item errors are the ones DECISIONS.md section 8 predicted:
- **Names that lie:** beer-battered cod, rum cake and the alcohol-free mojito read as alcohol. RCP-004, which should pass, is flagged.
- **Brand names:** Chianti, Barolo, Marlboro and the iPad are not found. RCP-024 drops from flag to review.
- **No translation:** the French and German food lines, and a Portuguese laundry line, become `other` or clothing ("camisas").
- **Category vs purpose:** "Dry cleaning - 3 shirts" is clothing, so a laundry receipt that should pass is flagged.
- **No recipients:** with no recipient, the gift to a public official on RCP-022 falls under GIFT-01's cap instead of GIFT-04. A receipt that should be flagged passes.

Where it does better: it costs nothing, runs in milliseconds, gives the same answer every time with no cache, and sends no receipt data to any outside service. On this data it is not more accurate on any measure.

## Real receipts: the CORD pilot (method fixed 2026-10-30, before any label or model call)

The 32 receipts are 57% violations, and nothing in them is unseen or messy. This pilot asks what v1 does on real receipt text, at the rate restricted items actually occur.

- **Data.** The 100 receipts in CORD v2's validation split (CC-BY-4.0): real receipts from Indonesian restaurants, cafés, bakeries and shops, 270 lines after conversion. Text only (`cord/README.md`).
  - The train split is too large for the API that serves the text.
  - The test split stays unseen for a later held-out check.
- **Context.** CORD has none, so every receipt gets the same declared, synthetic context: a US employee, "Meals - Individual", MCC 5812, one attendee, IDR at 16,000 per USD, one employee per receipt. Under it, the only rule that can fire without an item cause is the $100 international meal cap (MEAL-01), on CORD-V041.
- **Labels.** The assistant labels every line, sure or unsure, following the guide in `cord/README.md`. The labels are committed before the first model call and never reach a prompt. The user then checks every restricted and every unsure label, plus a random 30 of the rest.
- **What runs.** v1 exactly as frozen: same prompts, model, rules, and threshold 0.5. Nothing is tuned on the pilot. The correct decisions come from the engine run on the labels (oracle mode), under the same context.
- **Metrics.**
  1. **Items:** accuracy and macro F1 on all lines and on sure lines, plus restricted precision and recall.
  2. **Receipts:** false alarms are receipts the labels pass but v1 flags, or sends to review; each count gets a Wilson interval. Flag precision is the share of v1's flags that the labels also flag.
  3. **Confidence:** accuracy by confidence band, and how many lines and receipts each threshold in the `SWEEP` grid would send to review.
  4. **Disagreements:** each one is listed with its raw text, gloss, v1 category and label.
- **Threshold rule, fixed now for a full run.** For each t in the `SWEEP` grid:
  - cost(t) = 10 × (lines at or above t whose restricted flag is wrong) + 1 × (receipts with any line below t).
  - The lowest cost wins, and ties go to the lower t. The result is also shown at weights of 5 and 20.
  - **On the pilot,** this is computed for illustration only.
  - **To be adopted,** a threshold must be chosen on the train split, checked on the unseen test split, and released as v2.

**Results** (`make cord`, `outputs/cord/v1/metrics.json`). The run took 200 calls and cost $0.0394, or $0.00039 per receipt; translation adds a third, because every line is glossed. The first attempt crashed on CORD-V048 (see the changes table).
- **Items:**
  - 252 / 270 lines right (93%), and 239 / 244 on sure labels (98%).
  - The one restricted line, the rice soap, was found.
  - Seven lines were wrongly called restricted, and only one of those has a sure label.
- **Receipts:**
  - The labels give 98 pass and 2 flag. v1 gives 87 pass, 8 flag and 5 review, and it finds both real flags.
  - False flags: 6 / 98 (95% CI 2.8–12.7%). False reviews: 5 / 98.
  - Flag precision: 2 / 8 (7–59%).
- **The six false flags** (CORD-V008 has two of these causes):
  - Four packaging lines read as `household_goods`: two plastic bags, a paper bag, and a plastic clamshell box.
  - A free birthday candle read as `household_goods`.
  - "*Rhum", an option on a bakery item, read as a spirit (ALC-01).
  - "TALAM UNGU", a purple steamed cake. It was glossed literally as "PURPLE TRAY" and called kitchenware at confidence 0.99.
- **How much rests on unsure labels.** Only the cake has a sure label. Suppose every unsure label were replaced by v1's answer (an after-the-fact check). Flag precision would then be 8 / 8. So the headline number turns on whether packaging is `household_goods`, and neither the taxonomy nor HOUSE-01 says.
- **The five reviews.** All come from EXC-03, on lines below 0.5: three unreadable codes ("TT", "SB 1 OR", "KENTHIR 2") and packaging on two receipts. That is EXC-03 doing its job. But the labels can never send a receipt to review, so these count against v1.
- **Confidence ranks well but is not calibrated.**
  - The 225 lines at 0.999 or above are 99% right; the 45 below are 64% right.
  - The confident errors are the boundary cases: plastic bags at 0.96–0.98, the cake at 0.99, and the clamshell box at 1.0.
- **Threshold rule, as an illustration.** At all three weights it picks 0.995. That cuts false flags from 6 to 1, but sends 30 of 100 receipts to review. It is not adopted: v1 stays at 0.5.

## What was checked by hand

- **Translations (M8, 2026-10-30).** I read all 11 English glosses in `review/translations.csv` against the originals and marked each `ok`. None needed a change. The assistant drafted the glosses with DeepSeek at temperature 0; I made the call.
- **Recipients (M4).** The three lines in `review/item_attributes.csv` that name a recipient were annotated from the line text: one client, two lines for a government official.

## AI collaboration log

What an assistant got wrong, and the check that caught it.

| Milestone | What went wrong | Caught by |
|---|---|---|
| M3 | `rules/rules.yaml` was drafted by the assistant. The EXC-01 and EXC-02 evidence quotes each closed with a period where the policy continues with a colon, so the quote was not verbatim. | `test_evidence_is_verbatim_policy_text` |
| M3 | The gate itself was mutation-tested: a wrong cap (75→76), a wrong MCC code, a wrong severity and a reworded quote each fail it. A truncated quote passes, as intended, since it is still verbatim. | Manual mutation run |
| M3 | The first draft put an `mccs` condition on ALC-01/02 `applies_to`. The schema has no such field, and the "any condition matches" rule would have retrieved them for any on-premises meal. On-premises scope moved to the engine. | Review against the `AppliesTo` schema (`extra="forbid"`) |
| M4 | The first draft of `build_context` wrapped the meal, OTC and baggage totals in one `try`. A receipt with no meal spend would then have dropped out of its trip's OTC total. Split into three. | Code review before the first test run |
| M4 | The engine matched the answer key on the first oracle run. So the tests were checked by sabotage instead: each of the 31 rule checks was disabled in turn, and every one fails at least one gate. Seven (ALC-01, DOC-01, GIFT-02, HOUSE-01, MCC-01, PERS-03, TECH-01) are caught only by the oracle, which is why it runs under pytest and not only as `make oracle`. | Mutation sweep |
| M5 | The retriever followed cross-references from every retrieved rule, including always-on ones, so DOC-03's example citation added TECH-01 to every receipt. It was harmless to the output (the gate passed) but made the retrieval log misleading. Citations are now followed only from rules the receipt matched specifically: 8.0 → 7.1 rules per receipt, recall still 100%. | Reading the retrieval log |
| M6 | The first explanation templates used `str.capitalize()`, which lowercased the recipient ("Gifts for a. moreau"). They also said "is" for lists of several lines. | Reading the oracle explanations |
| M6 | Two of the assistant's own gate tests were wrong, not the reporter. One expected REG-03's per-person amount in EUR, though it is USD against a EUR cap. The other compared Decimal facts with their JSON strings after a round trip. | Test failures, checked against the facts by hand |
| M7 | The smoke gate as planned ("logprobs give a probability between 0 and 1") passed on useless data. At temperature 0, DeepSeek returns logprob 0.0 for every chosen token and -9999 for every alternative, so every item, the trap included, scored 1.0. The classifier now runs at temperature 1, and the gate asserts that logprobs are informative there and degenerate at 0. | Reading the raw logprobs after a too-clean pass |
| M7 | The plan assumed OpenRouter's strict `json_schema`; DeepSeek's own API offers only `json_object`. The enum is now enforced by validation and a retry instead of during decoding. | DeepSeek API reference, read before writing the client |
| M9 | Two of the assistant's classifier tests were wrong. The "misaligned confidence" case wasn't misaligned, because `json.dumps` escaped the decoy quotes. The prompt-leak check flagged "Service charge" and "Greeting card", which the taxonomy table itself lists as typical examples, so they belong in the definitions. | Test failures, read before changing anything |
| M9 | The first offline-rerun test compared parsed rows. `read_jsonl` reads floats as Decimal, so equal confidences looked different. It now compares the written bytes, which is also what the gate asks for. | Test failure, diffed by hand |
| M10 | The first blame draft compared each extracted recipient with all three annotation fields (name, type and item kind). It would have listed every line as a recipient error. | Code review before the first test run |
| M13 | In the viewer, picking a row on the receipt list with the baseline selected opened the detail page showing the system's results. `st.switch_page` reruns without the sidebar widget's state, so the radio fell back to its default. The choice now lives in a plain session key. `AppTest.switch_page` keeps widget state and passes either way, so no test guards this; the fix was checked by hand in the browser. | Clicking through the app, not the tests |
| CORD pilot | `category_confidences` promised confidence 0 whenever the category tokens couldn't be lined up, but only one of its two failure paths did that. The other raised, and on the 49th real receipt it stopped the whole run. No v1 receipt had triggered it. | The first run on real receipts |
| M11 | The Wilson interval at 0 / 9 came back with a lower bound of 2.8e-17, not 0. The assistant's first fix clamped it to 0 or above, which changed nothing, because the error is positive. It is now rounded to 10 places. | `test_wilson_interval_for_one_in_nine`, run after each fix |

## Changes after the v1 freeze

| Version | Gold error that prompted it | What changed | Why |
|---|---|---|---|
| — | none | No change was made after seeing a gold error, so v1 is also the final version. | — |
| v1, crash fix (2026-10-30) | None. It was a crash on real data, not a gold error. On CORD-V048, the 49th pilot receipt, DeepSeek's logprob tokens ended with a newline that its reply text lacked. | `span_probability` now accepts trailing whitespace in the tokens. If the tokens still don't match, `category_confidences` gives every line confidence 0 (so EXC-03), as its docstring already said, instead of raising. | v1 stopped the whole batch on one receipt. The 32 receipts' outputs are byte-identical after the fix, so v1's results stand. |
