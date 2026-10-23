import unittest
from datetime import date
from decimal import Decimal

from invoicing.currency import convert
from invoicing.reports import aging
from tests.helpers import ACME, invoice


class Reports(unittest.TestCase):
    def test_aging(self):
        a = invoice(ACME, "A")
        a.add_line("x", 1, "100.00")
        b = invoice(ACME, "B", issued=date(2025, 6, 1))
        b.add_line("y", 1, "10.00")
        out = aging([a, b], date(2026, 2, 15))
        self.assertEqual(out["1-30"], Decimal("121.00"))
        self.assertEqual(out["90+"], Decimal("12.10"))


class Currency(unittest.TestCase):
    def test_convert(self):
        self.assertEqual(convert("100", "USD", "EUR"), Decimal("92.00"))
        self.assertEqual(convert("100", "EUR", "EUR"), Decimal("100.00"))

    def test_unknown(self):
        with self.assertRaises(KeyError):
            convert("1", "EUR", "JPY")
