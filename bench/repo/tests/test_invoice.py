import unittest
from datetime import date
from decimal import Decimal

from tests.helpers import ACME, GOLD, invoice


class Invoices(unittest.TestCase):
    def test_empty(self):
        inv = invoice()
        self.assertEqual(inv.total(), Decimal("0.00"))

    def test_due_date(self):
        self.assertEqual(invoice(issued=date(2026, 1, 1)).due, date(2026, 1, 31))

    def test_simple_total(self):
        inv = invoice(ACME)
        inv.add_line("widget", 2, "50.00")
        self.assertEqual(inv.subtotal(), Decimal("100.00"))
        self.assertEqual(inv.tax(), Decimal("21.00"))
        self.assertEqual(inv.total(), Decimal("121.00"))

    def test_gold_discount_before_tax(self):
        inv = invoice(GOLD)
        inv.add_line("widget", 1, "200.00")
        self.assertEqual(inv.discount(), Decimal("10.00"))
        self.assertEqual(inv.tax(), Decimal("36.10"))
        self.assertEqual(inv.total(), Decimal("226.10"))

    def test_mixed_rates(self):
        inv = invoice(ACME)
        inv.add_line("book", 1, "20.00", reduced=True)
        inv.add_line("pen", 1, "10.00")
        self.assertEqual(inv.tax(), Decimal("3.90"))

    def test_customer_statement_line(self):
        inv = invoice(ACME)
        inv.add_line("cable", 1, "10.50")
        self.assertEqual(inv.total(), Decimal("12.71"))

    def test_promo_line(self):
        inv = invoice(ACME)
        inv.add_line("chair", 4, "25.00", promo_percent="20")
        self.assertEqual(inv.subtotal(), Decimal("80.00"))

    def test_many_lines(self):
        inv = invoice(ACME)
        for i in range(10):
            inv.add_line("item %d" % i, 1, "1.00")
        self.assertEqual(inv.total(), Decimal("12.10"))
