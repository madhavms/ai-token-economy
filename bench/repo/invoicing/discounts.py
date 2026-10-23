"""Discounts. Line discounts come first, then the customer's loyalty discount on what is left."""
from .customers import loyalty_percent
from .money import percent_of, round_cents, to_money


def line_net(quantity, unit_price):
    """Quantity times unit price, before any discount."""
    return round_cents(to_money(quantity) * to_money(unit_price))


def apply_line_discounts(line):
    """Line-level discounts. Returns the discounted net amount of one line (a dict with quantity and unit_price)."""
    net = line_net(line["quantity"], line["unit_price"])
    if line.get("promo_percent"):
        net = round_cents(net - percent_of(net, line["promo_percent"]))
    return net


def loyalty_discount(customer, subtotal):
    """The loyalty discount on the subtotal after line discounts, in cents."""
    return round_cents(percent_of(subtotal, loyalty_percent(customer)))
