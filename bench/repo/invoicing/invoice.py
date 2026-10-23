"""Invoices: lines, discounts, tax and totals."""
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import config
from .discounts import apply_line_discounts, loyalty_discount
from .money import round_cents
from .tax import line_tax


@dataclass
class Invoice:
    number: str
    customer: object
    issued: date
    lines: list = field(default_factory=list)
    paid_on: date = None

    @property
    def due(self):
        return self.issued + timedelta(days=config.PAYMENT_TERMS_DAYS)

    def add_line(self, description, quantity, unit_price, reduced=False, promo_percent=None):
        self.lines.append({"description": description, "quantity": quantity, "unit_price": unit_price,
                           "reduced": reduced, "promo_percent": promo_percent})

    def line_nets(self):
        return [apply_line_discounts(l) for l in self.lines]

    def subtotal(self):
        return round_cents(sum(self.line_nets(), round_cents(0)))

    def discount(self):
        return loyalty_discount(self.customer, self.subtotal())

    def tax(self):
        """VAT per line on the line's share of the discounted subtotal."""
        sub = self.subtotal()
        if sub == 0:
            return round_cents(0)
        factor = (sub - self.discount()) / sub
        total = round_cents(0)
        for line, net in zip(self.lines, self.line_nets()):
            total += line_tax(round_cents(net * factor), self.customer.region, line["reduced"])
        return total

    def total(self):
        return round_cents(self.subtotal() - self.discount() + self.tax())
