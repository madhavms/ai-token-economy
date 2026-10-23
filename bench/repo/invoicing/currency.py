"""Display conversion only: invoices are always billed in the default currency."""
from decimal import Decimal

from .money import round_cents

TO_EUR = {"EUR": Decimal("1"), "USD": Decimal("0.92"), "GBP": Decimal("1.17"), "CHF": Decimal("1.04")}


def convert(amount, src, dst):
    if src not in TO_EUR or dst not in TO_EUR:
        raise KeyError("unknown currency")
    return round_cents(Decimal(amount) * TO_EUR[src] / TO_EUR[dst])
