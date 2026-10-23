"""VAT by region. Tax is worked out per line and each line's tax is rounded to cents."""
from decimal import Decimal

from .money import percent_of, to_money

RATES = {"NL": "21", "DE": "19", "FR": "20", "BE": "21", "LU": "17", "IE": "23"}
REDUCED = {"NL": "9", "DE": "7", "FR": "5.5", "BE": "6", "LU": "8", "IE": "13.5"}


def rate(region, reduced=False):
    table = REDUCED if reduced else RATES
    if region not in table:
        raise KeyError("no VAT rate for region %r" % region)
    return table[region]


def line_tax(net, region, reduced=False):
    """VAT on one line's net amount, in cents."""
    raw = percent_of(to_money(net), rate(region, reduced))
    return Decimal(round(raw, 2))
