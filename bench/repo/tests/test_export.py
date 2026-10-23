import unittest

from invoicing.export import to_csv
from tests.helpers import ACME, invoice


class Export(unittest.TestCase):
    def test_header_and_row(self):
        inv = invoice(ACME)
        inv.add_line("widget", 1, "10.00")
        text = to_csv([inv])
        lines = text.strip().split("\n")
        self.assertEqual(lines[0], "number;customer;issued;subtotal;discount;tax;total")
        self.assertEqual(lines[1], "INV-1;c-acme;2026-01-01;10.00;0.00;2.10;12.10")

    def test_empty(self):
        self.assertEqual(len(to_csv([]).strip().split("\n")), 1)
