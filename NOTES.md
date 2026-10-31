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

## AI collaboration log

What an assistant got wrong, and the check that caught it.

| Milestone | What went wrong | Caught by |
|---|---|---|
| M3 | `rules/rules.yaml` was drafted by the assistant. The EXC-01 and EXC-02 evidence quotes each closed with a period where the policy continues with a colon, so the quote was not verbatim. | `test_evidence_is_verbatim_policy_text` |
| M3 | The first draft put an `mccs` condition on ALC-01/02 `applies_to`. The schema has no such field, and the "any condition matches" rule would have retrieved them for any on-premises meal. On-premises scope moved to the engine. | Review against the `AppliesTo` schema (`extra="forbid"`) |
| M3 | The gate itself was mutation-tested: a wrong cap (75→76), a wrong MCC code, a wrong severity and a reworded quote each fail it. A truncated quote passes, as intended, since it is still verbatim. | Manual mutation run |

## Changes after the v1 freeze

| Version | Gold error that prompted it | What changed | Why |
|---|---|---|---|
