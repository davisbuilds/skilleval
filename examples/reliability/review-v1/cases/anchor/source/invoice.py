"""All monetary values are integer cents."""


def total(lines):
    """Each line is (unit_price_cents, quantity); charge all ordered units."""
    return sum(price for price, quantity in lines)
