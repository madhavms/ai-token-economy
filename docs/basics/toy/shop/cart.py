"""A tiny shopping cart."""


def total(items):
    """Sum of price * quantity for each (price, quantity) pair, in pence."""
    return sum(price for price, qty in items)


def with_vat(pence, rate=0.20):
    """Add VAT and round to the nearest penny."""
    return round(pence * (1 + rate))
