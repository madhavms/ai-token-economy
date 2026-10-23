"""Money helpers. All amounts are Decimal; cents are rounded half up, never half to even."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(value):
    """Turn an int, str or Decimal into a Decimal amount (floats are refused: they can't hold cents exactly)."""
    if isinstance(value, float):
        raise TypeError("use str or Decimal for money, not float")
    return Decimal(value)


def round_cents(amount):
    """Round to whole cents, half up: 0.125 -> 0.13."""
    return to_money(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def percent_of(amount, percent):
    """``percent`` of ``amount``, unrounded."""
    return to_money(amount) * to_money(percent) / Decimal(100)


def split_evenly(amount, parts):
    """Split into ``parts`` cent amounts that add back up exactly; the first parts carry any extra cent."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    total = round_cents(amount)
    base = (total / parts).quantize(CENT, rounding="ROUND_DOWN")
    out = [base] * parts
    left = total - base * parts
    i = 0
    while left > 0:
        out[i] += CENT
        left -= CENT
        i += 1
    return out
