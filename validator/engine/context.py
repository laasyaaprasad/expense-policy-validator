"""Pass 1 of the engine: each receipt as the rules see it, and the totals that span receipts.

MEAL-01, REG-02 and REG-03 add up one employee's meal spend per calendar date; HEALTH-01 adds up OTC
spend across a trip; the PERS-01/02 baggage-delay allowance is shared per delay reference. So every
receipt is viewed first, the totals are built from all of them, and only then is any receipt judged.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from validator.data import LineItem
from validator.engine.money import to_usd
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.rules_config import RulesConfig

FOOD_AND_DRINK = ("food_beverage", "alcohol")
MEAL_SPEND = ("food_beverage", "alcohol", "service_fee")  # MEAL-01: food, drink and gratuity lines
BAGGAGE_ALLOWANCE = ("personal_care", "clothing")  # PERS-02: one allowance shared by both


class NotEvaluable(Exception):
    """The receipt lacks something a rule needs, such as a conversion rate or a date."""


def line_order(line_id: str) -> tuple[str, int]:
    """Sort key that puts RCP-014-L10 after RCP-014-L9."""
    head, _, number = line_id.rpartition("-L")
    return (head, int(number)) if number.isdigit() else (line_id, 0)


class ReceiptView:
    """One receipt with its classifications. Lines below the confidence threshold are left out of
    every rule's calculation (EXC-03); a non-itemized line is never uncertain."""

    def __init__(self, classified: ClassifiedReceipt, config: RulesConfig, threshold: float,
                 hidden: frozenset[str] = frozenset()):
        self._classified, self.threshold = classified, threshold
        self.receipt = classified.receipt
        self.items = classified.items
        self.config = config
        ordered = sorted(self.receipt.line_items, key=lambda line: line_order(line.line_id))
        self.uncertain = tuple(
            line.line_id for line in ordered
            if self.receipt.itemized and self.items[line.line_id].confidence < threshold
        )
        self.lines = tuple(line for line in ordered if line.line_id not in self.uncertain and line.line_id not in hidden)

    def without(self, line_ids: set[str]) -> "ReceiptView":
        """The same receipt with some lines removed, for a rule another rule replaces on those lines."""
        return ReceiptView(self._classified, self.config, self.threshold, frozenset(line_ids))

    # --- identity and context -------------------------------------------------------------------

    @property
    def receipt_id(self) -> str:
        return self.receipt.receipt_id

    @property
    def region(self) -> str:
        return self.config.region_of(self.receipt.employee_country)

    @property
    def expense_type(self) -> str:
        return self.receipt.submitted_expense_type

    @property
    def reference(self) -> str | None:
        return self.receipt.pre_approval_ref

    @property
    def reference_kind(self) -> str | None:
        """What the recorded reference is (EXC-02), read from its prefix. Unknown prefixes satisfy nothing."""
        if self.reference is None:
            return None
        return next((kind for prefix, kind in self.config.reference_prefixes.items() if self.reference.startswith(prefix)), None)

    @property
    def date(self) -> date:
        if self.receipt.transaction_datetime is None:
            raise NotEvaluable("no transaction date recorded (DOC-02)")
        return self.receipt.transaction_datetime.date()

    @property
    def on_premises(self) -> bool:
        """Restaurants, bars and hotels serve alcohol on the premises (ALC-01/02 scope, MCC-03)."""
        return self.receipt.mcc in self.config.on_premises_mccs

    @property
    def international(self) -> bool:
        """MEAL-01: the receipt's currency differs from the currency of the employee's home country."""
        if self.receipt.currency is None:
            raise NotEvaluable("no currency recorded (DOC-04)")
        return self.receipt.currency != self.config.home_currency[self.receipt.employee_country]

    # --- lines ------------------------------------------------------------------------------------

    def item(self, line: LineItem) -> ClassifiedItem:
        return self.items[line.line_id]

    def with_category(self, *categories: str) -> tuple[LineItem, ...]:
        return tuple(line for line in self.lines if self.item(line).category in categories)

    def with_kind(self, kind: str) -> tuple[LineItem, ...]:
        return tuple(line for line in self.lines if self.item(line).item_kind == kind)

    def otc_lines(self) -> tuple[LineItem, ...]:
        return tuple(line for line in self.with_category("health_otc") if self.item(line).item_kind != "prescription_medication")

    # --- money ------------------------------------------------------------------------------------

    @property
    def rate(self) -> Decimal | None:
        if self.receipt.fx_rate_to_usd is not None:
            return self.receipt.fx_rate_to_usd
        return Decimal(1) if self.receipt.currency == "USD" else None

    def usd(self, amount: Decimal) -> Decimal:
        if self.rate is None:
            raise NotEvaluable("no conversion rate recorded (DOC-04)")
        return to_usd(amount, self.rate)

    def line_usd(self, line: LineItem) -> Decimal:
        return self.usd(line.amount)

    def sum_usd(self, lines: tuple[LineItem, ...]) -> Decimal:
        return sum((self.line_usd(line) for line in lines), Decimal(0))

    def total_usd(self) -> Decimal:
        if self.receipt.total_amount is None:
            raise NotEvaluable("no receipt total recorded (DOC-02)")
        return self.usd(self.receipt.total_amount)

    def certain_total_usd(self) -> Decimal:
        """The receipt total less any uncertain lines (EXC-03 leaves them out of every calculation)."""
        return self.total_usd() - sum((self.usd(line.amount) for line in self.receipt.line_items if line.line_id in self.uncertain), Decimal(0))

    # --- meals (MEAL-01 definitions, shared by REG-02 and REG-03) ---------------------------------

    @property
    def is_meal_receipt(self) -> bool:
        if self.expense_type not in self.config.meal_expense_types:
            return False
        return not self.receipt.itemized or bool(self.with_category(*FOOD_AND_DRINK))

    def meal_spend_usd(self) -> Decimal:
        if not self.receipt.itemized:
            return self.total_usd()
        return self.sum_usd(self.with_category(*MEAL_SPEND))

    @property
    def people(self) -> int:
        """Per person means divided by the attendee count, or by one when none is recorded."""
        return self.receipt.attendee_count or 1


@dataclass(frozen=True)
class MealEntry:
    receipt_id: str
    per_person_usd: Decimal
    international: bool


@dataclass
class Context:
    meal_days: dict[tuple[str, date], list[MealEntry]] = field(default_factory=lambda: defaultdict(list))
    otc_by_trip: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    baggage_by_reference: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    euro_rates: dict[date, Decimal] = field(default_factory=dict)

    def meal_day(self, r: ReceiptView) -> list[MealEntry]:
        """Every meal receipt by this employee on this receipt's date, including this one."""
        entries = self.meal_days.get((r.receipt.employee_id, r.date), [])
        if not any(entry.receipt_id == r.receipt_id for entry in entries):
            raise NotEvaluable("meal spend could not be computed")
        return entries

    def euro_rate(self, r: ReceiptView) -> Decimal:
        """The euro rate on the transaction date: a euro receipt's own rate, else one seen on that date."""
        if r.receipt.currency == "EUR" and r.rate is not None:
            return r.rate
        if r.date not in self.euro_rates:
            raise NotEvaluable(f"no euro rate known for {r.date}")
        return self.euro_rates[r.date]


def build_context(views: list[ReceiptView]) -> Context:
    ctx = Context()
    for r in sorted(views, key=lambda view: view.receipt_id):
        receipt = r.receipt
        if receipt.currency == "EUR" and receipt.fx_rate_to_usd is not None and receipt.transaction_datetime:
            ctx.euro_rates.setdefault(r.date, receipt.fx_rate_to_usd)
        # Each total is built on its own: a receipt missing what one needs still counts towards the
        # others, and its own checks report what is missing.
        try:
            if r.is_meal_receipt:
                entry = MealEntry(r.receipt_id, r.meal_spend_usd() / r.people, r.international)
                ctx.meal_days[(receipt.employee_id, r.date)].append(entry)
        except NotEvaluable:
            pass
        try:
            if receipt.trip_id is not None and r.otc_lines():
                ctx.otc_by_trip[receipt.trip_id] += r.sum_usd(r.otc_lines())
        except NotEvaluable:
            pass
        try:
            if r.reference_kind == "baggage_delay":
                ctx.baggage_by_reference[r.reference] += r.sum_usd(r.with_category(*BAGGAGE_ALLOWANCE))
        except NotEvaluable:
            pass
    return ctx
