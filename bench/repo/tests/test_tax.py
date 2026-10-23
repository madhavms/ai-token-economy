import unittest
from decimal import Decimal

from invoicing import tax


class Tax(unittest.TestCase):
    def test_rates(self):
        self.assertEqual(tax.rate("NL"), "21")
        self.assertEqual(tax.rate("DE", reduced=True), "7")

    def test_unknown_region(self):
        with self.assertRaises(KeyError):
            tax.rate("XX")

    def test_simple_line(self):
        self.assertEqual(tax.line_tax("100.00", "NL"), Decimal("21.00"))

    def test_reduced_line(self):
        self.assertEqual(tax.line_tax("100.00", "FR", reduced=True), Decimal("5.50"))

    def test_half_cent_rounds_up(self):
        # 10.50 x 21% = 2.205: the customer's statement says 2.21
        self.assertEqual(tax.line_tax("10.50", "NL"), Decimal("2.21"))

    def test_half_cent_rounds_up_reduced(self):
        # 2.50 x 9% = 0.225 -> 0.23
        self.assertEqual(tax.line_tax("2.50", "NL", reduced=True), Decimal("0.23"))

    def test_zero(self):
        self.assertEqual(tax.line_tax("0", "DE"), Decimal("0.00"))

    def test_all_regions_have_reduced(self):
        self.assertEqual(set(tax.RATES), set(tax.REDUCED))
