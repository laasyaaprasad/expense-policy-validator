"""The policy engine on hand-built inputs. Each test names the policy sentence it relies on."""

import itertools
from decimal import Decimal

import pytest

from tests.builders import CONFIG, from_data, line, receipt
from validator.data import load_receipts
from validator.engine import evaluate
from validator.engine.disposition import disposition
from validator.engine.money import to_usd
from validator.engine.rules import CHECKS
from validator.models import ClassifiedReceipt


def decide(*receipts: ClassifiedReceipt, config=CONFIG, **kwargs):
    decisions = evaluate(list(receipts), config, **kwargs)
    return decisions[0] if len(decisions) == 1 else decisions


def raised(decision) -> dict[str, tuple[str, ...]]:
    """Rule ID → line suffixes it cites, e.g. {"PERS-02": ("L1", "L2")}."""
    return {f.rule_id: tuple(line_id.rpartition("-")[2] for line_id in f.line_ids) for f in decision.exceptions}


def signals(decision) -> set[str]:
    return {s.rule_id for s in decision.review_signals}


# --- the five cases the brief names ---------------------------------------------------------------

def test_carve_out_without_its_evidence_rcp_027():
    # PERS-02: the baggage-delay exception needs "the delay reference is recorded (see EXC-02)". None is.
    d = decide(from_data("RCP-027", L1="clothing", L2="clothing"))
    assert raised(d) == {"PERS-02": ("L1", "L2")}
    assert d.disposition == "flag"


def test_regional_override_rcp_016():
    # REG-01: "For APAC employees REG-01 replaces ALC-01, ALC-02 and ALC-03: report REG-01 instead of those three."
    apac = from_data("RCP-016", L1="food_beverage", L2="alcohol", L3="service_fee")
    d = decide(apac)
    assert raised(d) == {"REG-01": ("L2",)}
    assert d.disposition == "flag"
    # The same dinner for a US employee: 3 attendees, $16.20 of beer, so ALC-01 and ALC-02 allow it.
    global_receipt = apac.receipt.model_copy(update={"employee_country": "US", "entity": "GLOBAL"})
    d = decide(ClassifiedReceipt(receipt=global_receipt, items=apac.items))
    assert raised(d) == {}
    assert d.disposition == "pass"


def test_threshold_boundary_rcp_006_and_rcp_026():
    # TECH-02: "reimbursable up to $200 per item ... Any accessory or peripheral above $200 requires a pre-approval reference"
    dock = decide(from_data("RCP-006", L1="computer_accessory"))
    assert dock.disposition == "pass" and raised(dock) == {}
    monitor = decide(from_data("RCP-026", L1="computer_accessory", L2="computer_accessory"))
    assert raised(monitor) == {"TECH-02": ("L1",)}
    assert monitor.disposition == "flag"


def test_severity_ladder_rcp_031():
    # EXC-01: "only low exceptions ... → review". LAUNDRY-01 is low; the trip is 2 nights.
    d = decide(from_data("RCP-031", L1="laundry_service"))
    assert raised(d) == {"LAUNDRY-01": ("L1",)}
    assert d.disposition == "review"


def test_review_signal_on_its_own_rcp_028():
    # MCC-02: "A medium-risk MCC raises a review signal only." OTC on a trip, $14.48 in total, is under $25.
    d = decide(from_data("RCP-028", L1="health_otc", L2="health_otc"))
    assert raised(d) == {}
    assert signals(d) == {"MCC-02"}
    assert d.disposition == "review"


# --- EXC-01 -----------------------------------------------------------------------------------------

SEVERITIES = ["critical", "high", "medium", "low"]


@pytest.mark.parametrize("has_signals", [False, True])
@pytest.mark.parametrize("severities", [set(c) for n in range(5) for c in itertools.combinations(SEVERITIES, n)])
def test_disposition_truth_table(severities, has_signals):
    if severities & {"critical", "high", "medium"}:
        expected = "flag"
    elif severities or has_signals:
        expected = "review"
    else:
        expected = "pass"
    assert disposition(severities, has_signals) == expected


def test_disposition_rejects_an_unknown_severity():
    with pytest.raises(ValueError):
        disposition({"severe"}, False)


# --- thresholds and currency ------------------------------------------------------------------------

@pytest.mark.parametrize("amount, expected", [("75.00", {}), ("75.01", {"MEAL-01": ()})])
def test_meal_cap_is_inclusive(amount, expected):
    # MEAL-01: "Spend above the cap is a medium exception." Exactly $75 is not above it.
    assert raised(decide(receipt("T", line("food_beverage", amount)))) == expected


@pytest.mark.parametrize("unit_price, expected", [("200.00", {}), ("200.01", {"TECH-02": ("L1",)})])
def test_peripheral_cap_is_inclusive(unit_price, expected):
    # TECH-02: "reimbursable up to $200 per item".
    r = receipt("T", line("computer_accessory", unit_price), submitted_expense_type="Computer Equipment", mcc="5943")
    assert raised(decide(r)) == expected


def test_peripheral_cap_uses_unit_price_not_line_total():
    # TECH-02: "judged on the item's unit price in USD". Three $150 keyboards are $450 but each is under $200.
    r = receipt("T", line("computer_accessory", "450.00", quantity=3), submitted_expense_type="Computer Equipment", mcc="5943")
    assert raised(decide(r)) == {}


@pytest.mark.parametrize("alcohol, expected", [("60.00", {}), ("60.02", {"ALC-02": ("L2",)})])
def test_alcohol_per_attendee_cap_is_inclusive(alcohol, expected):
    # ALC-02: "capped at $30 per attendee: the USD total of the alcohol lines, divided by the attendee count."
    r = receipt("T", line("food_beverage", "80.00"), line("alcohol", alcohol),
                submitted_expense_type="Client Entertainment", attendee_count=2)
    assert raised(decide(r)) == expected


def test_currency_conversion():
    # REG-05 / DOC-04: thresholds are evaluated on the USD amount at the receipt's own rate.
    assert to_usd(Decimal("89800"), Decimal("0.0067")) == Decimal("601.66")
    assert to_usd(Decimal("0.125"), Decimal("1")) == Decimal("0.13")  # half up, not banker's rounding


def test_engine_conversion_matches_every_given_usd_amount():
    for r in load_receipts():
        for item in r.line_items:
            assert to_usd(item.amount, r.fx_rate_to_usd) == item.amount_usd, item.line_id
        assert to_usd(r.total_amount, r.fx_rate_to_usd) == r.total_amount_usd, r.receipt_id


def test_cap_crossed_only_after_conversion():
    # A $190 dock priced in EUR at 1.08 is $205.20: over the TECH-02 cap.
    r = receipt("T", line("computer_accessory", "190.00"), submitted_expense_type="Computer Equipment",
                mcc="5943", currency="EUR", fx_rate_to_usd="1.08")
    d = decide(r)
    assert raised(d) == {"TECH-02": ("L1",)}
    assert d.exceptions[0].facts["unit_price_usd"] == {"T-L1": Decimal("205.20")}


# --- determinism -----------------------------------------------------------------------------------

def _mixed_receipts():
    return [
        from_data("RCP-014", L1="household_goods", L2="household_goods", L3="household_goods", L4="alcohol"),
        from_data("RCP-016", L1="food_beverage", L2="alcohol", L3="service_fee"),
        from_data("RCP-001", L1="food_beverage", L2="food_beverage", L3="food_beverage", L4="service_fee"),
        from_data("RCP-002", L1="food_beverage", L2="alcohol", L3="service_fee"),
    ]


def test_same_input_gives_identical_output():
    assert evaluate(_mixed_receipts(), CONFIG) == evaluate(_mixed_receipts(), CONFIG)


def test_line_and_receipt_order_do_not_matter():
    shuffled = [
        ClassifiedReceipt(receipt=c.receipt.model_copy(update={"line_items": tuple(reversed(c.receipt.line_items))}), items=c.items)
        for c in reversed(_mixed_receipts())
    ]
    assert evaluate(shuffled, CONFIG) == evaluate(_mixed_receipts(), CONFIG)


# --- EXC-03 ----------------------------------------------------------------------------------------

def test_uncertain_item_raises_a_signal_and_is_left_out_of_totals():
    # EXC-03: "raise a review signal ... rather than an exception, and leave that item out of every other rule's calculation"
    certain = decide(receipt("T", line("food_beverage", "70.00"), line("food_beverage", "20.00")))
    assert raised(certain) == {"MEAL-01": ()}
    unsure = decide(receipt("T", line("food_beverage", "70.00"), line("food_beverage", "20.00", confidence=0.3)), threshold=0.7)
    assert raised(unsure) == {}
    assert signals(unsure) == {"EXC-03"}
    assert unsure.review_signals[0].line_ids == ("T-L2",)
    assert unsure.disposition == "review"


def test_uncertain_item_raises_no_exception_of_its_own():
    d = decide(receipt("T", line("food_beverage", "20.00"), line("alcohol", "9.00", confidence=0.5)), threshold=0.7)
    assert raised(d) == {}
    assert signals(d) == {"EXC-03"}


def test_uncertain_items_are_left_out_of_cross_receipt_totals():
    # Two meals on one day: $50 certain, plus $40 of which $30 is uncertain. Day total $60, under $75.
    first = receipt("A", line("food_beverage", "50.00"))
    second = receipt("B", line("food_beverage", "10.00"), line("food_beverage", "30.00", confidence=0.2))
    a, b = decide(first, second, threshold=0.7)
    assert raised(a) == {} and raised(b) == {}


def test_non_itemized_charge_never_raises_exc_03():
    # EXC-03: "A non-itemized charge is not an uncertain category: it is other by definition, and raises no EXC-03 signal."
    d = decide(receipt("T", line("other", "40.00", confidence=0.0), itemized=False), threshold=0.7)
    assert "EXC-03" not in signals(d)


# --- EXC-02 approval references -----------------------------------------------------------------------

PA_REF = {"pre_approval_ref": "PA-2026-0001"}


def test_pre_approval_waives_tech_02():
    d = decide(receipt("T", line("computer_accessory", "329.00"), submitted_expense_type="Computer Equipment", mcc="5943", **PA_REF))
    assert raised(d) == {}
    assert [(w.rule_id, w.reference) for w in d.waived] == [("TECH-02", "PA-2026-0001")]
    assert d.disposition == "pass"


@pytest.mark.parametrize("rule_id, r", [
    ("ENT-01", receipt("T", line("other", "200.00", item_kind="entertainment_venue"),
                       submitted_expense_type="Client Entertainment", mcc="7992", attendee_count=2, **PA_REF)),
    ("TECH-04", receipt("T", line("software_subscription", "144.00"), submitted_expense_type="Software", mcc="5734", **PA_REF)),
    ("PERS-02", receipt("T", line("clothing", "68.00"), submitted_expense_type="Travel - Other", mcc="5651", **PA_REF)),
    ("ALC-03", receipt("T", line("alcohol", "72.00"), submitted_expense_type="Client Entertainment", mcc="5921",
                       attendee_count=6, **PA_REF)),
])
def test_pre_approval_waives_nothing_else(rule_id, r):
    # EXC-02: "provided it is the kind the rule asks for ... A reference does not waive any other rule."
    d = decide(r)
    assert rule_id in raised(d)
    assert d.waived == ()


BAGGAGE = CONFIG.model_copy(update={"reference_prefixes": {**CONFIG.reference_prefixes, "BD-": "baggage_delay"}})


def test_baggage_delay_allowance_is_shared_between_toiletries_and_clothing():
    # PERS-02: "one $150 allowance per delay, shared between toiletries and clothing. Above it, the exception cites every line involved."
    # The data holds no baggage-delay reference, so this test gives the kind a prefix of its own.
    within = receipt("T", line("personal_care", "60.00"), line("clothing", "80.00"),
                     submitted_expense_type="Travel - Other", mcc="5311", pre_approval_ref="BD-1")
    d = decide(within, config=BAGGAGE)
    assert raised(d) == {}
    assert {w.rule_id for w in d.waived} == {"PERS-01", "PERS-02"}

    over = receipt("T", line("personal_care", "60.00"), line("clothing", "80.00"), line("clothing", "20.00"),
                   submitted_expense_type="Travel - Other", mcc="5311", pre_approval_ref="BD-1")
    assert raised(decide(over, config=BAGGAGE)) == {"PERS-01": ("L1",), "PERS-02": ("L2", "L3")}


def test_baggage_delay_allowance_spans_receipts():
    first = receipt("A", line("personal_care", "100.00"), submitted_expense_type="Travel - Other", mcc="5912", pre_approval_ref="BD-1")
    second = receipt("B", line("clothing", "60.00"), submitted_expense_type="Travel - Other", mcc="5311", pre_approval_ref="BD-1")
    a, b = decide(first, second, config=BAGGAGE)
    assert raised(a) == {"PERS-01": ("L1",)} and raised(b) == {"PERS-02": ("L1",)}


# --- cross-receipt totals ------------------------------------------------------------------------------

def test_meals_on_one_day_are_added_together():
    # MEAL-01: "per day means the per-person amounts of all of one employee's meal receipts on the same
    # calendar date, added together ... raise MEAL-01 on every meal receipt that contributes to that day."
    lunch = receipt("A", line("food_beverage", "40.00"), transaction_datetime="2026-04-14T12:00:00")
    dinner = receipt("B", line("food_beverage", "40.00"), transaction_datetime="2026-04-14T19:00:00", merchant_name="Elsewhere")
    next_day = receipt("C", line("food_beverage", "40.00"), transaction_datetime="2026-04-15T12:00:00")
    other_employee = receipt("D", line("food_beverage", "40.00"), employee_id="E-OTHER")
    a, b, c, d = decide(lunch, dinner, next_day, other_employee)
    assert raised(a) == raised(b) == {"MEAL-01": ()}
    assert raised(c) == raised(d) == {}
    assert a.exceptions[0].facts["receipts"] == ["A", "B"]


def test_mixed_day_uses_the_higher_cap():
    # MEAL-01: "on a day that mixes the two, the higher cap applies." $50 + EUR 40 ($43.20) = $93.20, under $100.
    domestic = receipt("A", line("food_beverage", "50.00"))
    abroad = receipt("B", line("food_beverage", "40.00"), currency="EUR", fx_rate_to_usd="1.08")
    a, b = decide(domestic, abroad)
    assert raised(a) == raised(b) == {}


def test_otc_spend_is_added_across_a_trip():
    # HEALTH-01: "up to $25 in total across all of that trip's receipts ... raised on each such receipt"
    first = receipt("A", line("health_otc", "15.00"), submitted_expense_type="Travel - Other", mcc="5999", trip_id="T1", trip_nights=3)
    second = receipt("B", line("health_otc", "12.00"), submitted_expense_type="Travel - Other", mcc="5999", trip_id="T1", trip_nights=3)
    other_trip = receipt("C", line("health_otc", "12.00"), submitted_expense_type="Travel - Other", mcc="5999", trip_id="T2", trip_nights=3)
    a, b, c = decide(first, second, other_trip)
    assert raised(a) == {"HEALTH-01": ("L1",)} and raised(b) == {"HEALTH-01": ("L1",)}
    assert raised(c) == {}
    assert a.exceptions[0].severity == "low"


def test_a_decision_does_not_depend_on_other_receipts_euro_rates():
    # REG-03: "Convert EUR 70 to USD at the euro rate on the transaction date". A GB employee's $300 dinner in
    # USD is over the cap whether or not someone else's euro receipt from that day is in the batch.
    dinner = receipt("A", line("food_beverage", "300.00"), employee_country="GB")
    coffee = receipt("B", line("food_beverage", "10.00"), employee_country="FR", employee_id="E-OTHER",
                     currency="EUR", fx_rate_to_usd="1.20")
    alone, (with_other, _) = decide(dinner), decide(dinner, coffee)
    assert raised(alone) == raised(with_other) and {"REG-03", "REG-04"} <= set(raised(alone))
    assert alone.exceptions == with_other.exceptions
    reg_03 = next(f for f in alone.exceptions if f.rule_id == "REG-03")
    assert reg_03.facts["euro_rate"] == CONFIG.euro_rate_usd


def test_a_split_is_shared_only_by_the_receipts_that_make_it_up():
    # TIME-03: "the $50 gift cap for one recipient (GIFT-01)". Two $30 gifts for Dana are a split; a $10 gift
    # for Alex at the same merchant that day is not part of it, and neither is a coffee there.
    gift = dict(submitted_expense_type="Client Gifts", mcc="5947", merchant_name="Gift Shop")
    dana = lambda rid, hour: receipt(rid, line("gift", "30.00", recipient_name="Dana Lee", recipient_type="client"),
                                     transaction_datetime=f"2026-04-14T{hour}:00:00", **gift)
    alex = receipt("C", line("gift", "10.00", recipient_name="Alex Kim", recipient_type="client"),
                   transaction_datetime="2026-04-14T12:00:00", **gift)
    coffee = receipt("D", line("food_beverage", "8.00"), merchant_name="Gift Shop", transaction_datetime="2026-04-14T13:00:00")
    a, b, c, d = decide(dana("A", 10), dana("B", 16), alex, coffee)
    assert raised(a) == raised(b) == {"TIME-03": ()}
    assert a.exceptions[0].facts["receipts"] == ["A", "B"]
    assert raised(c) == raised(d) == {}


def test_a_meal_split_is_found_beside_a_non_meal_receipt():
    # TIME-03: "the employee's meal cap, compared per person (MEAL-01 ...)". $74 and $71.50 meals split the $75
    # cap even when the same merchant also sold the employee something that isn't a meal.
    lunch = receipt("A", line("food_beverage", "74.00"), transaction_datetime="2026-04-14T12:00:00")
    dinner = receipt("B", line("food_beverage", "71.50"), transaction_datetime="2026-04-14T19:00:00")
    shop = receipt("C", line("office_supplies", "5.00"), submitted_expense_type="Office Supplies",
                   transaction_datetime="2026-04-14T15:00:00")
    a, b, c = decide(lunch, dinner, shop)
    assert raised(a) == raised(b) == {"TIME-03": ()}
    assert raised(c) == {}


def test_a_meal_split_is_measured_against_the_days_cap():
    # MEAL-01: "on a day that mixes the two, the higher cap applies." Two $40 meals at one merchant are $80,
    # over the $75 domestic cap, but a EUR 5 meal elsewhere that day makes the cap $100: no split, no breach.
    lunch = receipt("A", line("food_beverage", "40.00"), transaction_datetime="2026-04-14T12:00:00")
    dinner = receipt("B", line("food_beverage", "40.00"), transaction_datetime="2026-04-14T19:00:00")
    abroad = receipt("C", line("food_beverage", "5.00"), currency="EUR", fx_rate_to_usd="1.08", merchant_name="Elsewhere",
                     transaction_datetime="2026-04-14T09:00:00")
    assert all(raised(d) == {} for d in decide(lunch, dinner, abroad))


def test_a_gift_split_keeps_an_unrelated_gift_breach():
    # TIME-03 "replaces the threshold rule being split around", not GIFT-01's other test: "Any gift of $10 or
    # more must also name its recipient". The unnamed $15 gift is still a breach.
    gift = dict(submitted_expense_type="Client Gifts", mcc="5947", merchant_name="Gift Shop")
    dana = line("gift", "30.00", recipient_name="Dana Lee", recipient_type="client")
    a = receipt("A", dana, line("gift", "15.00"), transaction_datetime="2026-04-14T10:00:00", **gift)
    b = receipt("B", dana, transaction_datetime="2026-04-14T16:00:00", **gift)
    a, b = decide(a, b)
    assert raised(a) == {"TIME-03": (), "GIFT-01": ("L2",)}
    assert raised(b) == {"TIME-03": ()}


def test_laundry_needs_a_trip_record_not_just_a_night_count():
    # LAUNDRY-01: "On shorter trips, or on a receipt with no trip record, it is a low exception."
    r = receipt("T", line("laundry_service", "20.00"), submitted_expense_type="Travel - Other", mcc="7216",
                trip_id=None, trip_nights=4)
    assert raised(decide(r)) == {"LAUNDRY-01": ("L1",)}


def test_a_country_with_no_home_currency_is_not_evaluated_not_a_crash():
    # Data dictionary: "everyone else GLOBAL". MEAL-01 needs the home currency; without it, only that check stops.
    dinner = receipt("A", line("food_beverage", "120.00"), employee_country="CA", currency="CAD", fx_rate_to_usd="0.73")
    other = receipt("B", line("food_beverage", "120.00"), employee_id="E-OTHER")
    a, b = decide(dinner, other)
    assert {n.rule_id for n in a.not_evaluated} == {"MEAL-01"}
    assert raised(b) == {"MEAL-01": ()}


# --- overrides and retrieval ---------------------------------------------------------------------------

def test_gift_04_replaces_gift_01_for_its_lines():
    # GIFT-04: "replaces GIFT-01 for those lines". A $60 gift to an official is not also a GIFT-01 breach.
    r = receipt("T", line("gift", "60.00", recipient_name="R. Delgado", recipient_type="government_official"),
                submitted_expense_type="Client Gifts", mcc="5999")
    d = decide(r)
    assert raised(d) == {"GIFT-04": ("L1",)}
    assert d.exceptions[0].severity == "critical"


def test_override_holds_even_if_the_regional_rule_is_not_retrieved():
    # A retriever that returns ALC-01 but misses REG-01 must not bring the overridden global rule back.
    r = receipt("T", line("alcohol", "9.00"), employee_country="IN", currency="INR", fx_rate_to_usd="0.012")
    d = decide(r, retrieved={"T": {"ALC-01"}})
    assert raised(d) == {}
    assert d.rules_evaluated == ()


def test_every_rule_that_raises_has_exactly_one_check():
    assert set(CHECKS) == {rule.id for rule in CONFIG.rules if rule.kind != "mechanic"}
