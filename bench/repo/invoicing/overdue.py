"""Overdue rules and the aging buckets used by the dunning report."""
from datetime import timedelta

from .customers import grace_days

BUCKETS = ((0, "current"), (30, "1-30"), (60, "31-60"), (90, "61-90"))


def is_overdue(invoice, today):
    """Unpaid and past the due date plus the customer's grace period."""
    if invoice.paid_on is not None:
        return False
    return today > invoice.due + timedelta(days=grace_days(invoice.customer))


def days_late(invoice, today):
    return max(0, (today - invoice.due).days)


def bucket(invoice, today):
    late = days_late(invoice, today)
    label = "90+"
    for limit, name in BUCKETS:
        if late <= limit:
            label = name
            break
    return label
