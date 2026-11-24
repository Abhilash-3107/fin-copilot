"""How the server writes a rupee amount in a message, so every message reads the same way."""
from __future__ import annotations


def rupees(amount: float) -> str:
    """₹1,07,775.00 or -₹1,234.00: the sign in front of the rupee sign (the privacy mask looks
    for it there), the last three digits grouped and the rest in twos, like the interface."""
    cents = round(abs(amount) * 100)
    whole, fraction = divmod(cents, 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pairs = []
        while head:
            pairs.append(head[-2:])
            head = head[:-2]
        digits = ",".join(reversed(pairs)) + "," + tail
    sign = "-" if amount < 0 and cents else ""
    return f"{sign}₹{digits}.{fraction:02d}"
