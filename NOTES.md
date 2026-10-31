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

## AI collaboration log

What an assistant got wrong, and the check that caught it.

| Milestone | What went wrong | Caught by |
|---|---|---|
| M3 | `rules/rules.yaml` was drafted by the assistant. The EXC-01 and EXC-02 evidence quotes each closed with a period where the policy continues with a colon, so the quote was not verbatim. | `test_evidence_is_verbatim_policy_text` |
| M3 | The first draft put an `mccs` condition on ALC-01/02 `applies_to`. The schema has no such field, and the "any condition matches" rule would have retrieved them for any on-premises meal. On-premises scope moved to the engine. | Review against the `AppliesTo` schema (`extra="forbid"`) |
| M4 | The first draft of `build_context` wrapped the meal, OTC and baggage totals in one `try`. A receipt with no meal spend would then have dropped out of its trip's OTC total. Split into three. | Code review before the first test run |
| M4 | The engine matched the answer key on the first oracle run. So the tests were checked by sabotage instead: each of the 31 rule checks was disabled in turn, and every one fails at least one gate. Seven (ALC-01, DOC-01, GIFT-02, HOUSE-01, MCC-01, PERS-03, TECH-01) are caught only by the oracle, which is why it runs under pytest and not only as `make oracle`. | Mutation sweep |
| M5 | The retriever followed cross-references from every retrieved rule, including always-on ones, so DOC-03's example citation added TECH-01 to every receipt. It was harmless to the output (the gate passed) but made the retrieval log misleading. Citations are now followed only from rules the receipt matched specifically: 8.0 → 7.1 rules per receipt, recall still 100%. | Reading the retrieval log |
| M3 | The gate itself was mutation-tested: a wrong cap (75→76), a wrong MCC code, a wrong severity and a reworded quote each fail it. A truncated quote passes, as intended, since it is still verbatim. | Manual mutation run |

## Changes after the v1 freeze

| Version | Gold error that prompted it | What changed | Why |
|---|---|---|---|
