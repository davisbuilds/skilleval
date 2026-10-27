"""All values are integer cents; cart lines are (unit_cents, quantity)."""


def checkout(lines, shipping_cents, free_shipping_at=5000):
    subtotal = sum(unit for unit, quantity in lines)
    shipping = shipping_cents if subtotal <= free_shipping_at else 0
    return subtotal + shipping


def can_refund(paid_cents, previous_refunds_cents, requested_cents):
    return 0 < requested_cents <= paid_cents
