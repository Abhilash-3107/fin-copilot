"""One rupee formatter for every message the server words: sign first, Indian grouping."""
from __future__ import annotations

import pytest

from finance_copilot.money import rupees


@pytest.mark.parametrize("amount, text", [
    (0, "₹0.00"),
    (5, "₹5.00"),
    (999.5, "₹999.50"),
    (1000, "₹1,000.00"),
    (12345.6, "₹12,345.60"),
    (107775, "₹1,07,775.00"),
    (10000000, "₹1,00,00,000.00"),
    (-1234, "-₹1,234.00"),
    (-107775.5, "-₹1,07,775.50"),
    (-0.001, "₹0.00"),  # rounds to zero: no sign
])
def test_the_sign_comes_first_and_the_grouping_is_indian(amount, text):
    assert rupees(amount) == text
