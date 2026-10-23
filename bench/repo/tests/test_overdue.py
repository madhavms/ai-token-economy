import unittest
from datetime import date

from invoicing import overdue
from tests.helpers import ACME, BIG, invoice


class Overdue(unittest.TestCase):
    def test_paid_is_never_overdue(self):
        inv = invoice()
        inv.paid_on = date(2026, 1, 5)
        self.assertFalse(overdue.is_overdue(inv, date(2027, 1, 1)))

    def test_within_grace(self):
        self.assertFalse(overdue.is_overdue(invoice(ACME), date(2026, 2, 10)))

    def test_after_grace(self):
        self.assertTrue(overdue.is_overdue(invoice(ACME), date(2026, 2, 11)))

    def test_enterprise_longer_grace(self):
        self.assertFalse(overdue.is_overdue(invoice(BIG), date(2026, 2, 20)))

    def test_days_late(self):
        self.assertEqual(overdue.days_late(invoice(), date(2026, 2, 5)), 5)
        self.assertEqual(overdue.days_late(invoice(), date(2026, 1, 5)), 0)

    def test_buckets(self):
        self.assertEqual(overdue.bucket(invoice(), date(2026, 1, 20)), "current")
        self.assertEqual(overdue.bucket(invoice(), date(2026, 2, 15)), "1-30")
        self.assertEqual(overdue.bucket(invoice(), date(2026, 6, 1)), "90+")
