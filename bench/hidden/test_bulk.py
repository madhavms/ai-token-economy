"""Held-out check for the feature task. Copied into the trial only after the agent finishes."""
import unittest
from decimal import Decimal

from invoicing import discounts
from tests.helpers import ACME, GOLD, invoice


class Bulk(unittest.TestCase):
    def test_below_threshold_no_bulk(self):
        self.assertEqual(discounts.apply_line_discounts({"quantity": 99, "unit_price": "1.00"}), Decimal("99.00"))

    def test_at_threshold(self):
        self.assertEqual(discounts.apply_line_discounts({"quantity": 100, "unit_price": "1.00"}), Decimal("95.00"))

    def test_bulk_after_promo(self):
        line = {"quantity": 200, "unit_price": "2.50", "promo_percent": "10"}
        # 500.00 -> promo 450.00 -> bulk 5% = 427.50
        self.assertEqual(discounts.apply_line_discounts(line), Decimal("427.50"))

    def test_bulk_rounds_half_up(self):
        # 101 x 0.33 = 33.33; 5% = 1.6665 -> 33.33 - 1.6665 = 31.6635 -> 31.66
        self.assertEqual(discounts.apply_line_discounts({"quantity": 101, "unit_price": "0.33"}), Decimal("31.66"))

    def test_bulk_before_loyalty(self):
        inv = invoice(GOLD)
        inv.add_line("bolts", 100, "1.00")
        self.assertEqual(inv.subtotal(), Decimal("95.00"))
        self.assertEqual(inv.discount(), Decimal("4.75"))

    def test_small_order_unchanged(self):
        inv = invoice(ACME)
        inv.add_line("widget", 2, "50.00")
        self.assertEqual(inv.total(), Decimal("121.00"))
