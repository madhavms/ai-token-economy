"""CSV export of invoices for the accounting system."""
import csv
import io

from . import config


def rows(invoices):
    for inv in invoices:
        yield [inv.number, inv.customer.id, inv.issued.isoformat(), str(inv.subtotal()), str(inv.discount()),
               str(inv.tax()), str(inv.total())]


def to_csv(invoices):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=config.EXPORT_DELIMITER, lineterminator="\n")
    w.writerow(["number", "customer", "issued", "subtotal", "discount", "tax", "total"])
    for r in rows(invoices):
        w.writerow(r)
    return buf.getvalue()
