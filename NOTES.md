# Working notes

## Data conventions

One line per convention the data dictionary (`data/README.md`) settles. Where the data can confirm a convention, a test does; its name is in brackets.

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
11. MCC-02 is always its own review signal, even on a receipt that also has an exception. (Needs the MCC risk table, so it is tested with the engine, not the data.)
12. No answer uses EXC-03, so any EXC-03 signal we raise is unmatched, and so is any disposition change it causes.
13. A non-itemized charge is `other`, not uncertain. [`test_non_itemized_lines_are_other`]
14. HEALTH-01 is the only rule with two severities: low for OTC, medium for prescription. (Tested in `test_rules_config.py`.)
15. Matching: an exception matches on receipt + `rule_id`; its evidence is correct when `line_ids` are exactly equal (both empty for whole-receipt rules); signals match on receipt + `rule_id`; a gold rule never raised has recall 0 and precision n/a; a macro average is the mean of the per-category scores. (Implemented in `evaluation/`.)

## Interpretation decisions

Where the policy text leaves room, these are the readings the engine takes. All were made before the first oracle run, which matched the answer key on 28 / 28, so none was changed after a mismatch, though the assistant that drafted them had the answer key to hand. Each has a test.

| Rule | Decision | Policy sentence |
|---|---|---|
| EXC-03 | Uncertain lines are left out of receipt totals too (MEAL-02, REG-04), not only out of category tests. | "leave that item out of every other rule's calculation for the receipt" |
| EXC-04 | A rule that cannot be applied (no conversion rate, date or total) is reported under `not_evaluated`. It does not pass silently, and no exception is raised. | "An exception that cannot name its evidence must not be raised." |
| All caps | Caps are inclusive: exactly $75, $200, $30 per attendee, $150 or EUR 50 does not breach. | "Spend above the cap", "up to $200 per item", "above $150", "above EUR 50" |
| ALC-01 | Client Entertainment with an attendee count of 1 still breaks ALC-01. | "records an attendee count of two or more" |
| GIFT-01 | Applies to gifts for clients, for unnamed recipients, and for named recipients of unknown type. Gifts for employees go to GIFT-03; gifts for officials go to GIFT-04. | "Gifts to clients and prospects are capped at $50 per recipient per occasion." |
| GIFT-01 | A recipient is read only from the line's own text. No other field on the record could name one. | "in the line description or elsewhere on the expense" |
| GIFT-01, GIFT-03 | Gift cards are left out of both. Food and drink on a meal receipt, and service fees, are never gifts. | "Gift cards and other cash equivalents fall under GIFT-02 alone"; "Food, drink and gratuities consumed at a meal are not gifts" |
| HEALTH-01 | Every `health_otc` line counts as OTC medication, lozenges and first aid included. When OTC and prescription lines both break the rule on one receipt, one medium exception cites all of them. | "Each rule appears at most once per receipt." |
| PERS-01 | The baggage-delay carve-out covers every `personal_care` line, and the 24-hour delay is taken as given by the reference. No field records either. | "essential toiletries are reimbursable up to $150, provided the delay reference is recorded" |
| REG-03, REG-04 | A euro receipt uses its own rate. Any other receipt is converted at a fixed rate in `rules.yaml` (1.08, the rate on every euro receipt in the data), standing in for a dated rate feed. It first borrowed a euro receipt's rate from the same date, which made one receipt's decision depend on the rest of the batch. All EMEA receipts in the data are in euros. | "converted at the euro rate on the transaction date" |
| DOC-04 | A missing currency also raises DOC-04, because the receipt cannot be shown to be in USD. | "must record the transaction currency and the conversion rate used" |
| TIME-03 | *Added after the run.* Receipts are grouped on the exact `merchant_name`. The meal cap is compared per person, each receipt divided by its own attendee count, against the employee's cap for the whole day (regional where the addendum sets one; on a mixed day, the higher one). Only a meal cap is replaced, on the receipts that make up the split: it is the one limit that adds receipts together, so the split also breaches it. DOC-01 and GIFT-01 test each receipt alone, where each part is within the limit, so anything they find, such as an unnamed gift, is a separate breach and stays. A same-day meal at another merchant still counts towards MEAL-01's daily total; no receipt tests that case. | "It replaces the threshold rule being split around" |
| TIME-02 | *Added after the run.* Only the trip record decides it. The CORD receipts each get a trip of their own, since a US employee paying in rupiah is travelling. | "with no associated trip record" |

## EXC-03 threshold rule (written before the dev set was first run)

The rule is fixed here before any classification output has been seen, on the dev set or the receipts. Each candidate threshold t in {0.3, 0.4, …, 0.9} scores the number of wrong dev items with confidence below t, minus the number of right dev items below t. The best score wins, and a tie goes to the lower t (fewer signals). If no candidate scores above zero, the threshold stays at 0.5, the default argued in DECISIONS.md section 4. The real receipts are never used to choose or adjust it. Their threshold sweep is reported only as analysis after the freeze.

**Result.** All 43 dev items came back right on category, item kind and recipient in all three runs: as configured, a fresh repeat, and without merchant context. None flipped and the lowest confidence was 0.96, so every candidate scored 0 and the threshold stayed at 0.5 (`outputs/dev/threshold.json`). The dev set is too easy to tell thresholds apart, so this says nothing about whether 0.5 is a good threshold.

## The final run

The config was frozen at tag `frozen` (`outputs/frozen_config.json`), and the first classification of the real receipts was committed untouched (commit e6e8d34) before anything was scored. It took 31 calls, all valid first time, for $0.0074. A fresh repeat and a run without the merchant and expense type cost $0.0150 more (`outputs/classifications_{repeat,no_context}.jsonl`). The scores are in `docs/evaluation.md`.

Nothing was changed after seeing a gold error. The code has changed since the freeze (the crash fix the CORD pilot found, the per-receipt trace, tidying), and `outputs/classifications.jsonl` is byte-identical to the file first committed in e6e8d34 (then at `outputs/v1/classifications.jsonl`). `outputs/results.jsonl` matched 2723f6b until the timing rules (TIME-01 to 03), a stretch goal built afterwards from the policy text. They change the four unscored timing receipts, which now match their answers, and on the 28 scored receipts only the policy version and the list of rules evaluated. The fixes found by the final reviews (a missing home currency, the batch-borrowed euro rate, TIME-03's grouping and replacement, LAUNDRY-01's trip check) changed only the policy version.

**The OpenRouter rerun.** The brief's OpenRouter key arrived after all of the above, so the frozen config was rerun through it unchanged, on the same model, and that run is now the committed one; the first run is in git history (e6e8d34 and later). The smoke test found that OpenRouter's own routing sent a call to CoreWeave, which returned no logprobs, so the client pins Fireworks, then Together, the two that did. Fireworks served all 347 calls (32 receipts, the dev set, both variants and CORD) for $0.0528. The first run's client and its 349 cached responses were then removed, so the validator calls OpenRouter only; the first run's outputs and spend stay in git history, and the same removal corrected CORD's cost summary, which had been counting both runs' translations. All 79 categories and all 32 decisions are unchanged, so no scored number moved. On the dev set one item changed: "Whiskey stones gift set" came back `gift` instead of `household_goods`, at 0.43, below the threshold, so EXC-03 would catch it; the threshold rule still picks 0.5. On CORD, 13 of 270 lines changed category.

**The switch to temperature 0.** Experiment 6 (`experiments/semantic_first/`) tested the classifier at temperature 0 against every test set, with its bar committed before any call. I chose to switch it alone and not adopt the experiment's semantic first stage, which failed its own bar. The committed outputs now replay that experiment's calls: no category or decision on the 32 receipts changed, the dev set went from 42 to 43, and CORD from 250 lines and 13 false alarms to 252 and 12.

**The CORD label check.** I checked the 57 labels the method names (every unsure or restricted one and a random 30) one at a time against the taxonomy and the labelling guide, with the assistant showing each line's receipt and the label but never the system's answer. It came after the CORD results were known, so it isn't blind. 52 stand and 5 changed, none restricted: both bare "TT" lines became `other`, since nothing on their receipts says what they are (the assistant had guessed Thai tea from other shops' prices); "Kwan Yin Cup" and "F.Wngr GES-HC" became sure; "ISI CAMPUR" became unsure. Lines right went from 248 to 250 of 270; every receipt-level number is unchanged.

## Keyword baseline

The word list (`rules/keywords.yaml`) was committed in 2ce89b0, before its first run on the receipts, and has no brand names. The assistant that wrote it had already seen the receipt lines (printed for the prompt-leakage check), and two entries may owe something to that: `ipa`, and the German compound `weißwein`, the only compound in the list. If anything, its score is flattering. Its 16 item errors are the kinds DECISIONS.md section 9 predicted (`docs/evaluation.md`, section 5).

## Real receipts (CORD)

To get more data than the 32 receipts, the system was run unchanged on 100 real receipts from CORD v2. The method was fixed before any label or model call (commit 9fc0708) and is in `cord/README.md`, with the results. The pilot found one crash (last row of "What the checks caught") and left one open question: whether a free ($0) line should raise an item exception.

## What was checked by hand

- **Translations.** The assistant drafted the 11 English glosses in `review/translations.csv` with the same model at temperature 0. I signed all 11 off as `ok`, and none was changed.
- **Recipients.** The three lines in `review/item_attributes.csv` that name a recipient were annotated from the line text: one client, and two lines for a government official.
- **Not checked: the CORD labels.** The assistant wrote them, following the guide in `cord/README.md`. I haven't checked the restricted and unsure ones in `review/cord_labels.csv`, so the CORD numbers rest on the assistant's labels.

## How I steered the assistant

Claude Code did most of the building. These are the points where I directed it, chose between its options, overrode its plan or corrected it.

| Stage | What I did | What changed |
|---|---|---|
| Research | **Directed:** research how expense systems are built in 2026 and recommend proven practice, not over-engineering, before any design. | Every system looked at lets AI read the receipt and keeps limits and lists as configuration, so this one does too (DECISIONS.md). |
| Research | **Chose:** pushed for DeepSeek V4.1 Flash, which scores well with reasoning off and so spends few tokens, and named Jev as the one to compare. | DeepSeek V4.1 Flash, at 156 output tokens a receipt. A head-to-head ruled Jev out on many categories and non-English text. |
| Planning | **Directed:** build in milestones that can each be tested, so a failure can be traced to its stage. | The engine was built and tested first, on gold categories (oracle mode), then the blame report. |
| Planning | **Corrected:** the plan had no guard against tuning on the 32 labelled receipts, which are also the only test set. I asked what we would test against. | Prompts and the threshold are developed only on a hand-written dev set, and the config was frozen and its first real run committed before scoring. |
| After the freeze | **Directed:** get more real data to check the system on. **Overrode** its plan for about 2,000 calls and 3,500 hand-labelled lines with a 100-receipt pilot. | The CORD pilot, which exposed a crash none of the 32 had hit. |
| After the pilot | **Directed:** were the false flags really the system's fault? | Four of the six rest only on $0 lines, which became an open question, not a fix. |
| After the pilot | **Corrected:** it proposed changing the engine and both prompts to fix CORD's errors. I stopped it before any of it was built: why fit the system to receipts the brief isn't about? | Nothing is tuned on CORD. |
| Before submitting | **Directed:** has anything overfit? | An audit. Labels never reach the system, and the real risks (a prompt written with the receipts in view, an engine that isn't blind, a small n) are stated in the README and evaluation. |
| Before submitting | **Asked** whether a cleaner rewrite would be better. Kept the design, and **directed** it to build the timing rules cleanly. | TIME-01 to 03, on the existing engine; all four timing receipts match. TIME-04 can't apply. |
| Before submitting | **Corrected:** asked what observability was missing, it proposed adding monitoring to the viewer, which the brief doesn't ask for and which would have shown made-up reviewer verdicts. I kept it internal. | A per-receipt trace (`outputs/traces.jsonl`); dashboards and reviewer verdicts are in the AWS page's monitoring plan. |
| Before submitting | **Directed:** an independent check, by two fresh agents, for overfitting and against the brief. | No leakage or hardcoding found. Two engine bugs and several stale or overstated claims, all fixed (see below). |

| Before submitting | **Directed:** run on the brief's OpenRouter key, keep the same model, and compare it with the first run and the baseline. **Chose** OpenRouter as the submitted run. | The smoke test caught a host that drops logprobs; the client now pins Fireworks, then Together. Decisions unchanged on all 32. |
| Before submitting | **Checked by hand:** the 57 CORD labels the method names. **Corrected** the assistant's guess that a bare "TT" was Thai tea. | 5 labels changed, none restricted; CORD lines right 248 → 250. |

Standing rules throughout: the API key is never committed (the assistant added the OpenRouter key to `.env` at my request), labels are used only for scoring, the organisers' data and brief stay out of the repo, and downloaded files are treated as untrusted.

## What the checks caught

The assistant's own bugs, and what caught them.

| Stage | What went wrong | Caught by |
|---|---|---|
| Rules config | The EXC-01 and EXC-02 evidence quotes in the assistant's `rules/rules.yaml` each ended with a period where the policy has a colon, so they were not verbatim. | `test_evidence_is_verbatim_policy_text` |
| Model client | The planned smoke test ("logprobs give a probability between 0 and 1") passed on useless data. At temperature 0, the first run's API returned logprob 0.0 for every chosen token, so every item, the trap included, scored 1.0. The classifier then ran at temperature 1, and the test checks that logprobs are informative there; after experiment 6 it runs at 0 on the pinned hosts. | The assistant reading the raw logprobs after a too-clean pass |
| Model client | The plan assumed strict `json_schema` output, which the first run's API doesn't offer. The category list is now enforced by validation and one retry. | The API reference, read before writing the client |
| Timing rules | TIME-02 would have sent all 100 CORD receipts to review: their synthetic context had no trip. Each now has a trip of its own, and the CORD results are unchanged. | A check of which receipts each new rule would touch, before building it |
| Engine | An employee from a country missing from `home_currency` stopped the whole run with a `KeyError`, though the data dictionary expects "everyone else". MEAL-01 is now `not_evaluated` for that receipt. And a GB employee's $300 dinner in USD passed alone but was flagged next to someone else's euro receipt from that day, because the euro rate was borrowed from the batch. It is now a fixed configured rate. Both have tests; no decision on the 32 receipts changed. | An independent review by two agents, run before submitting, probing with receipts not in the data |
| Engine and scorer | TIME-03 gave a gift split for one recipient to every receipt at that merchant, including a gift for someone else, and missed a meal split when the merchant also sold the employee something else. LAUNDRY-01 accepted a night count on a receipt with no trip record. The item scorer skipped a missing line instead of refusing the result. A third review found two more TIME-03 cases: it took the meal cap from the merchant's receipts, not the whole day, so a mixed day's higher cap was missed; and it replaced all of GIFT-01, hiding an unnamed gift on the same receipt. All fixed, with tests; no decision changed. | Two more outside reviews, probing with receipts not in the data |
| CORD pilot | One of `category_confidences`' two failure paths raised instead of giving confidence 0, and on the 49th real receipt it stopped the whole run. Both paths now give confidence 0. None of the 32 receipts had hit it, and their outputs are unchanged. | The first run on real receipts |
