import unittest
from decimal import Decimal

from invoicing.money import percent_of, round_cents, split_evenly, to_money


class Money(unittest.TestCase):
    def test_round_half_up(self):
        self.assertEqual(round_cents("0.125"), Decimal("0.13"))
        self.assertEqual(round_cents("0.135"), Decimal("0.14"))
        self.assertEqual(round_cents("2.205"), Decimal("2.21"))

    def test_round_negative(self):
        self.assertEqual(round_cents("-0.125"), Decimal("-0.13"))

    def test_float_refused(self):
        with self.assertRaises(TypeError):
            to_money(0.1)

    def test_percent_of(self):
        self.assertEqual(percent_of("200", "5"), Decimal("10"))
        self.assertEqual(percent_of("10.50", "21"), Decimal("2.205"))

    def test_split_adds_up(self):
        parts = split_evenly("100.00", 3)
        self.assertEqual(sum(parts), Decimal("100.00"))
        self.assertEqual(parts[0], Decimal("33.34"))

    def test_split_rejects_zero(self):
        with self.assertRaises(ValueError):
            split_evenly("1", 0)

    def test_split_single(self):
        self.assertEqual(split_evenly("9.99", 1), [Decimal("9.99")])

    def test_split_small_amount(self):
        self.assertEqual(split_evenly("0.02", 3), [Decimal("0.01"), Decimal("0.01"), Decimal("0.00")])
