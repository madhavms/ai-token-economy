"""Aging report: open amounts per bucket."""
from collections import OrderedDict

from .money import round_cents
from .overdue import BUCKETS, bucket


def aging(invoices, today):
    out = OrderedDict((name, round_cents(0)) for _, name in BUCKETS)
    out["90+"] = round_cents(0)
    for inv in invoices:
        if inv.paid_on is None:
            out[bucket(inv, today)] += inv.total()
    return out
