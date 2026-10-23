"""Customer records and the rules that depend on who the customer is."""
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    region: str = "NL"
    tier: str = "bronze"          # bronze | silver | gold
    enterprise: bool = False


def grace_days(customer):
    """Days after the due date before this customer's invoice is overdue. Enterprise contracts get longer."""
    if customer.enterprise:
        return 30
    return config.DEFAULT_GRACE_DAYS


def loyalty_percent(customer):
    return config.LOYALTY_PERCENT.get(customer.tier, "0")
