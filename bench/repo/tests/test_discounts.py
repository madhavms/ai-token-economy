import unittest
from decimal import Decimal

from invoicing import discounts
from tests.helpers import ACME, BIG, GOLD


class Discounts(unittest.TestCase):
    def test_line_net(self):
        self.assertEqual(discounts.line_net(3, "9.99"), Decimal("29.97"))

    def test_promo(self):
        line = {"quantity": 2, "unit_price": "50.00", "promo_percent": "10"}
        self.assertEqual(discounts.apply_line_discounts(line), Decimal("90.00"))

    def test_no_promo(self):
        line = {"quantity": 1, "unit_price": "12.34"}
        self.assertEqual(discounts.apply_line_discounts(line), Decimal("12.34"))

    def test_loyalty_bronze_is_zero(self):
        self.assertEqual(discounts.loyalty_discount(ACME, Decimal("100")), Decimal("0.00"))

    def test_loyalty_gold(self):
        self.assertEqual(discounts.loyalty_discount(GOLD, Decimal("100")), Decimal("5.00"))

    def test_loyalty_silver(self):
        self.assertEqual(discounts.loyalty_discount(BIG, Decimal("80.25")), Decimal("1.61"))

    def test_promo_rounding(self):
        line = {"quantity": 3, "unit_price": "3.33", "promo_percent": "15"}
        self.assertEqual(discounts.apply_line_discounts(line), Decimal("8.49"))
