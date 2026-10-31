"""USD conversion. All policy thresholds are evaluated on the USD amount (DOC-04), at the receipt's rate (REG-05)."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_usd(amount: Decimal, rate: Decimal) -> Decimal:
    return (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)


def cents(amount: Decimal) -> Decimal:
    """Rounded for display; comparisons use the exact value."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)
