"""One check per rule ID. A check returns an Outcome when its rule is broken or is satisfied by a
recorded reference, and None when the rule has nothing to say about the receipt.

Checks see only the lines the engine hands them: uncertain lines (EXC-03) and lines another rule has
replaced (GIFT-04 over GIFT-01) are already gone. Thresholds and severities come from rules.yaml.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from validator.data import LineItem
from validator.engine.context import FOOD_AND_DRINK, Context, MealEntry, NotEvaluable, ReceiptView
from validator.engine.money import cents
from validator.rules_config import RuleSpec


@dataclass(frozen=True)
class Outcome:
    line_ids: tuple[str, ...] = ()
    facts: dict = field(default_factory=dict)
    severity: str | None = None  # only for HEALTH-01, the one rule with two severities
    waived_by: str | None = None  # the reference that satisfied the rule (EXC-02)
    replaces: tuple[str, ...] = ()  # rules this outcome replaces on the receipt (TIME-03)


Check = Callable[[RuleSpec, ReceiptView, Context], Outcome | None]
CHECKS: dict[str, Check] = {}


def check(rule_id: str) -> Callable[[Check], Check]:
    def register(fn: Check) -> Check:
        CHECKS[rule_id] = fn
        return fn
    return register


def _ids(lines) -> tuple[str, ...]:
    return tuple(line.line_id for line in lines)


def _broken(lines, **facts) -> Outcome | None:
    return Outcome(_ids(lines), facts) if lines else None


def _needs_reference(rule: RuleSpec, r: ReceiptView, lines, **facts) -> Outcome | None:
    """EXC-02: a recorded reference of the kind the rule asks for satisfies it. Any other kind waives nothing."""
    if not lines:
        return None
    if r.reference_kind == rule.requires_reference:
        return Outcome(_ids(lines), facts, waived_by=r.reference)
    facts["required_reference"] = rule.requires_reference
    if r.reference is not None:
        facts.update(reference=r.reference, reference_kind=r.reference_kind)
    return Outcome(_ids(lines), facts)


# --- meals -------------------------------------------------------------------------------------

def _meal_01_cap(rule: RuleSpec, r: ReceiptView, ctx: Context, entries: list[MealEntry]):
    # "on a day that mixes the two, the higher cap applies"
    return rule.params["international_cap_usd"] if any(e.international for e in entries) else rule.params["domestic_cap_usd"]


def _reg_03_cap(rule: RuleSpec, r: ReceiptView, ctx: Context, entries: list[MealEntry]):
    # "Convert EUR 70 to USD at the euro rate on the transaction date"
    return rule.params["cap_eur"] * ctx.euro_rate(r)


# The daily meal cap each rule sets, in USD per person.
MEAL_CAPS = {"MEAL-01": _meal_01_cap, "REG-02": lambda rule, r, ctx, entries: rule.params["cap_usd"], "REG-03": _reg_03_cap}


def _meal_cap(rule: RuleSpec, r: ReceiptView, ctx: Context, **facts) -> Outcome | None:
    """MEAL-01 definitions: per-person amounts of one employee's meal receipts on one date, added together."""
    if not r.is_meal_receipt:
        return None
    entries = ctx.meal_day(r)
    day_total = sum(entry.per_person_usd for entry in entries)
    cap = MEAL_CAPS[rule.id](rule, r, ctx, entries)
    if day_total <= cap:
        return None
    return Outcome((), {
        "date": r.date.isoformat(),
        "per_person_day_usd": cents(day_total),
        "cap_usd": cents(cap),
        "receipts": sorted(entry.receipt_id for entry in entries),
        **facts,
    })


@check("MEAL-01")
def meal_01(rule, r, ctx):
    return _meal_cap(rule, r, ctx)


@check("REG-02")
def reg_02(rule, r, ctx):
    return _meal_cap(rule, r, ctx)


@check("REG-03")
def reg_03(rule, r, ctx):
    return _meal_cap(rule, r, ctx, cap_eur=rule.params["cap_eur"], euro_rate=ctx.euro_rate(r))


@check("MEAL-02")
def meal_02(rule, r, ctx):
    if not r.is_meal_receipt or r.receipt.attendee_count is not None:
        return None
    total = r.certain_total_usd()
    if total <= rule.params["total_threshold_usd"]:
        return None
    return Outcome((), {"total_usd": cents(total), "threshold_usd": rule.params["total_threshold_usd"]})


# --- alcohol -----------------------------------------------------------------------------------

def _alcohol_permitted(r: ReceiptView) -> bool:
    """ALC-01: only on a receipt declared as Client Entertainment that records two or more attendees."""
    minimum = r.config.rule("ALC-01").params["min_attendees"]
    return r.expense_type == "Client Entertainment" and (r.receipt.attendee_count or 0) >= minimum


@check("ALC-01")
def alc_01(rule, r, ctx):
    alcohol = r.with_category("alcohol")
    if not alcohol or not r.on_premises or _alcohol_permitted(r):
        return None
    return _broken(alcohol, expense_type=r.expense_type, attendee_count=r.receipt.attendee_count)


@check("ALC-02")
def alc_02(rule, r, ctx):
    alcohol = r.with_category("alcohol")
    if not alcohol or not r.on_premises or not _alcohol_permitted(r):
        return None
    total = r.sum_usd(alcohol)
    per_attendee = total / r.receipt.attendee_count
    if per_attendee <= rule.params["cap_per_attendee_usd"]:
        return None
    return _broken(alcohol, alcohol_usd=cents(total), attendee_count=r.receipt.attendee_count,
                   per_attendee_usd=cents(per_attendee), cap_usd=rule.params["cap_per_attendee_usd"])


@check("ALC-03")
def alc_03(rule, r, ctx):
    # MCC-03: a bar (5813) is on the premises, never retail.
    if r.on_premises:
        return None
    return _broken(r.with_category("alcohol"), mcc=r.receipt.mcc, mcc_description=r.receipt.mcc_description)


@check("REG-01")
def reg_01(rule, r, ctx):
    return _broken(r.with_category("alcohol"), entity=r.region)


# --- entertainment -----------------------------------------------------------------------------

@check("ENT-01")
def ent_01(rule, r, ctx):
    return _needs_reference(rule, r, r.with_kind("entertainment_venue"))


@check("ENT-02")
def ent_02(rule, r, ctx):
    return _broken(r.with_category("gambling"))


# --- technology --------------------------------------------------------------------------------

@check("TECH-01")
def tech_01(rule, r, ctx):
    return _broken(r.with_category("personal_electronics"))


@check("TECH-02")
def tech_02(rule, r, ctx):
    # "reimbursable up to $200 per item, judged on the item's unit price in USD"
    cap = rule.params["unit_price_cap_usd"]
    over = tuple(line for line in r.with_category("computer_accessory") if r.usd(line.unit_price) > cap)
    unit_prices = {line.line_id: r.usd(line.unit_price) for line in over}
    return _needs_reference(rule, r, over, unit_price_usd=unit_prices, cap_usd=cap)


@check("TECH-03")
def tech_03(rule, r, ctx):
    return _broken(r.with_kind("mobile_phone"))


@check("TECH-04")
def tech_04(rule, r, ctx):
    return _needs_reference(rule, r, r.with_category("software_subscription"))


# --- gifts -------------------------------------------------------------------------------------

def _gifts(r: ReceiptView) -> tuple[LineItem, ...]:
    """An item is a gift when the receipt is declared as Client Gifts or the line names who it is for.
    Food, drink and gratuities consumed at a meal are not gifts; gift cards fall under GIFT-02 alone."""
    gifts = []
    for line in r.lines:
        item = r.item(line)
        if item.category in ("service_fee", "gift_card"):
            continue
        if r.expense_type in r.config.meal_expense_types and item.category in FOOD_AND_DRINK:
            continue
        if r.expense_type == "Client Gifts" or item.recipient_name:
            gifts.append(line)
    return tuple(gifts)


def _recipient_key(name: str) -> str:
    """Lines that name the same recipient are added together, however the name is punctuated."""
    return " ".join(re.sub(r"[^\w\s]", " ", name.lower()).split())


@check("GIFT-01")
def gift_01(rule, r, ctx):
    cap, name_from = rule.params["cap_per_recipient_usd"], rule.params["name_required_from_usd"]
    gifts = [line for line in _gifts(r) if r.item(line).recipient_type != "employee"]  # employees: GIFT-03
    named, cited = defaultdict(list), set()
    over_cap, unnamed = [], []
    for line in gifts:
        name = r.item(line).recipient_name
        if name:
            named[_recipient_key(name)].append(line)
        elif r.line_usd(line) >= name_from:
            # "Unnamed gifts are each tested on their own": the name test, and the cap for that line alone.
            cited.add(line.line_id)
            unnamed.append(line.line_id)
            if r.line_usd(line) > cap:
                over_cap.append({"recipient": None, "line_ids": [line.line_id], "total_usd": r.line_usd(line)})
    for lines in named.values():
        total = r.sum_usd(tuple(lines))
        if total > cap:
            cited |= set(_ids(lines))
            over_cap.append({"recipient": r.item(lines[0]).recipient_name, "line_ids": list(_ids(lines)), "total_usd": total})
    if not cited:
        return None
    return Outcome(tuple(cited), {"cap_usd": cap, "over_cap": over_cap, "unnamed_line_ids": unnamed, "name_required_from_usd": name_from})


@check("GIFT-02")
def gift_02(rule, r, ctx):
    return _broken(r.with_category("gift_card"))


@check("GIFT-03")
def gift_03(rule, r, ctx):
    return _broken(tuple(line for line in _gifts(r) if r.item(line).recipient_type == "employee"))


@check("GIFT-04")
def gift_04(rule, r, ctx):
    # Every line provided to the official, meals included, but not a gratuity (EXC-04).
    lines = tuple(
        line for line in r.lines
        if r.item(line).recipient_type == "government_official" and r.item(line).category != "service_fee"
    )
    return _broken(lines, recipients=sorted({r.item(line).recipient_name or "" for line in lines}))


# --- non-reimbursable items --------------------------------------------------------------------

def _baggage_carve_out(rule: RuleSpec, r: ReceiptView, ctx: Context, lines) -> Outcome | None:
    """PERS-01/02: with a baggage-delay reference, one $150 allowance per delay covers toiletries and
    clothing together. Above it, the exception cites every line involved."""
    if not lines:
        return None
    facts = {"required_reference": rule.requires_reference}
    if r.reference_kind == rule.requires_reference:
        used, allowance = ctx.baggage_by_reference[r.reference], rule.params["baggage_allowance_usd"]
        if used <= allowance:
            return Outcome(_ids(lines), {"allowance_usd": allowance, "used_usd": used}, waived_by=r.reference)
        facts.update(reference=r.reference, allowance_usd=allowance, used_usd=used)
    elif r.reference is not None:
        facts.update(reference=r.reference, reference_kind=r.reference_kind)
    return Outcome(_ids(lines), facts)


@check("PERS-01")
def pers_01(rule, r, ctx):
    return _baggage_carve_out(rule, r, ctx, r.with_category("personal_care"))


@check("PERS-02")
def pers_02(rule, r, ctx):
    return _baggage_carve_out(rule, r, ctx, r.with_category("clothing"))


@check("PERS-03")
def pers_03(rule, r, ctx):
    return _broken(r.with_category("tobacco"))


@check("HOUSE-01")
def house_01(rule, r, ctx):
    return _broken(r.with_category("household_goods"))


@check("HEALTH-01")
def health_01(rule, r, ctx):
    otc, prescription = r.otc_lines(), r.with_kind("prescription_medication")
    cited, facts = list(prescription), {}
    if otc:
        trip = r.receipt.trip_id
        if trip is None:
            cited += otc
            facts["trip_id"] = None
        elif ctx.otc_by_trip[trip] > rule.params["trip_cap_usd"]:
            cited += otc
            facts.update(trip_id=trip, trip_otc_usd=ctx.otc_by_trip[trip], cap_usd=rule.params["trip_cap_usd"])
    if not cited:
        return None
    if prescription:
        facts["prescription_line_ids"] = list(_ids(prescription))
    severity = rule.severity["prescription"] if prescription else rule.severity["otc"]
    return Outcome(_ids(cited), facts, severity=severity)


@check("LAUNDRY-01")
def laundry_01(rule, r, ctx):
    # "On shorter trips, or on a receipt with no trip record, it is a low exception."
    nights = r.receipt.trip_nights if r.receipt.trip_id is not None else None
    if nights is not None and nights >= rule.params["min_trip_nights"]:
        return None
    return _broken(r.with_category("laundry_service"), trip_nights=nights, min_trip_nights=rule.params["min_trip_nights"])


# --- merchant category -------------------------------------------------------------------------

@check("MCC-01")
def mcc_01(rule, r, ctx):
    if r.config.mcc_tier(r.receipt.mcc) != "high":
        return None
    return Outcome((), {"mcc": r.receipt.mcc, "mcc_description": r.receipt.mcc_description})


@check("MCC-02")
def mcc_02(rule, r, ctx):
    if r.config.mcc_tier(r.receipt.mcc) != "medium":
        return None
    return Outcome((), {"mcc": r.receipt.mcc, "mcc_description": r.receipt.mcc_description})


# --- documentation -----------------------------------------------------------------------------

@check("DOC-01")
def doc_01(rule, r, ctx):
    if r.receipt.itemized:
        return None
    total = r.total_usd()
    if total <= rule.params["total_threshold_usd"]:
        return None
    return Outcome((), {"total_usd": total, "threshold_usd": rule.params["total_threshold_usd"]})


@check("DOC-02")
def doc_02(rule, r, ctx):
    fields = {
        "merchant name": r.receipt.merchant_name,
        "transaction date": r.receipt.transaction_datetime,
        "total": r.receipt.total_amount,
    }
    missing = [name for name, value in fields.items() if value is None]
    return Outcome((), {"missing": missing}) if missing else None


@check("DOC-03")
def doc_03(rule, r, ctx):
    covers = r.config.expense_type_covers.get(r.expense_type)
    if covers is None:
        raise NotEvaluable(f"expense type {r.expense_type!r} is not in the DOC-03 table")
    # "Service fees, gratuities and items in the other category are ignored"
    tested = [r.item(line).category for line in r.lines if r.item(line).category not in ("service_fee", "other")]
    if not tested or any(category in covers for category in tested):
        return None
    return Outcome((), {"expense_type": r.expense_type, "covers": list(covers), "categories": sorted(set(tested))})


@check("DOC-04")
def doc_04(rule, r, ctx):
    if r.receipt.currency == "USD":
        return None
    missing = [name for name, value in (("currency", r.receipt.currency), ("conversion rate", r.receipt.fx_rate_to_usd)) if value is None]
    return Outcome((), {"missing": missing}) if missing else None


@check("REG-04")
def reg_04(rule, r, ctx):
    # The data holds no VAT number: "Where the expense record does not capture a VAT number, treat it as missing."
    if r.receipt.currency == "EUR":
        total_eur = r.receipt.total_amount
        if total_eur is None:
            raise NotEvaluable("no receipt total recorded (DOC-02)")
        total_eur -= sum((line.amount for line in r.receipt.line_items if line.line_id in r.uncertain), 0)
    else:
        total_eur = r.certain_total_usd() / ctx.euro_rate(r)
    if total_eur <= rule.params["threshold_eur"]:
        return None
    return Outcome((), {"total_eur": cents(total_eur), "threshold_eur": rule.params["threshold_eur"], "vat_number": None})


# --- transaction patterns ----------------------------------------------------------------------

@check("TIME-01")
def time_01(rule, r, ctx):
    # "Transaction times are recorded in local time at the merchant", so the hour is read as recorded.
    when = r.receipt.transaction_datetime
    if when is None:
        raise NotEvaluable("no transaction time recorded (DOC-02)")
    if when.hour >= rule.params["window_end_hour"]:
        return None
    return Outcome((), {"time": when.strftime("%H:%M")})


@check("TIME-02")
def time_02(rule, r, ctx):
    # "Team meals and client entertainment often happen without travel and are not covered."
    if r.expense_type not in rule.applies_to.expense_types or r.receipt.trip_id is not None:
        return None
    return Outcome((), {"expense_type": r.expense_type})


def _split_thresholds(r: ReceiptView, group: list[ReceiptView], ctx: Context):
    """TIME-03's thresholds that apply to the group: the rule, each receipt's amount, and the limit."""
    config = r.config
    if not any(g.receipt.itemized for g in group):
        yield "DOC-01", {g.receipt_id: g.total_usd() for g in group}, config.rule("DOC-01").params["total_threshold_usd"]
    meals = [g for g in group if g.is_meal_receipt]
    if r.is_meal_receipt and len(meals) > 1:
        # The employee's meal cap is the regional one where the addendum replaces MEAL-01.
        meal = next((rule for rule in config.rules if "MEAL-01" in rule.overrides and r.region in rule.applies_to.regions),
                    config.rule("MEAL-01"))
        ids = {g.receipt_id for g in meals}
        entries = [entry for entry in ctx.meal_day(r) if entry.receipt_id in ids]
        if len(entries) != len(meals):
            raise NotEvaluable("meal spend could not be computed for every meal receipt in the group")
        # The cap is the day's, set by all its meals: on a day that mixes the two, the higher one.
        yield meal.id, {e.receipt_id: e.per_person_usd for e in entries}, MEAL_CAPS[meal.id](meal, r, ctx, ctx.meal_day(r))
    by_recipient = defaultdict(lambda: defaultdict(Decimal))
    for g in group:
        for line in _gifts(g):
            item = g.item(line)
            if item.recipient_name and item.recipient_type != "employee":  # GIFT-01's named gifts
                by_recipient[_recipient_key(item.recipient_name)][g.receipt_id] += g.line_usd(line)
    for amounts in by_recipient.values():
        if len(amounts) > 1:
            yield "GIFT-01", dict(amounts), config.rule("GIFT-01").params["cap_per_recipient_usd"]


@check("TIME-03")
def time_03(rule, r, ctx):
    # "It replaces the threshold rule being split around: report the split, not a separate breach of
    # that threshold."
    if not r.receipt.merchant_name:
        raise NotEvaluable("no merchant name recorded (DOC-02)")
    group = ctx.merchant_day(r)
    if len(group) < 2:
        return None
    # Only the receipts that make up a split share it: a gift to someone else at the same merchant doesn't.
    split = [
        {"rule_id": rule_id, "amounts_usd": {k: cents(v) for k, v in amounts.items()}, "limit_usd": cents(limit)}
        for rule_id, amounts, limit in _split_thresholds(r, group, ctx)
        if r.receipt_id in amounts and max(amounts.values()) <= limit < sum(amounts.values())
    ]
    if not split:
        return None
    receipts = sorted({receipt_id for s in split for receipt_id in s["amounts_usd"]})
    facts = {"merchant": r.receipt.merchant_name, "date": r.date.isoformat(), "receipts": receipts, "split_around": split}
    # Only a meal cap adds receipts together, so only it can also be breached by the split itself. DOC-01 and
    # GIFT-01 test each receipt alone, where each part is within the limit: whatever they find is another breach.
    return Outcome((), facts, replaces=tuple(s["rule_id"] for s in split if s["rule_id"] in MEAL_CAPS))
