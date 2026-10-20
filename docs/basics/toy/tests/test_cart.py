import unittest

from shop.cart import total, with_vat


class CartTest(unittest.TestCase):
    def test_total_counts_quantity(self):
        self.assertEqual(total([(250, 2), (100, 3)]), 800)

    def test_vat(self):
        self.assertEqual(with_vat(800), 960)


if __name__ == "__main__":
    unittest.main()
