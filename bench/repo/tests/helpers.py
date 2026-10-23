from datetime import date

from invoicing.customers import Customer
from invoicing.invoice import Invoice

ACME = Customer("c-acme", "Acme BV", region="NL", tier="bronze")
GOLD = Customer("c-gold", "Goldfinch GmbH", region="DE", tier="gold")
BIG = Customer("c-big", "Bigcorp SA", region="FR", tier="silver", enterprise=True)


def invoice(customer=ACME, number="INV-1", issued=date(2026, 1, 1)):
    return Invoice(number, customer, issued)
