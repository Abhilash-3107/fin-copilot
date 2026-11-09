"""UPI metadata for storage in transactions.upi_meta.

A thin serializer over parsers/description.py, which owns the grammar.
"""
from __future__ import annotations

import json

from finance_copilot.parsers.description import parse_description


def parse_upi_description(raw: str, noise_keywords: list[str]) -> str | None:
    """Return a JSON string of UPI metadata if raw is a UPI line, else None.

    Structure:
        {
          "vpa":   "merchant@okaxis",  # counterparty VPA if any token is one
          "ref":   "118030236405",     # UPI reference number
          "note":  "some free text",   # note after the ref, None if boilerplate noise
          "bank":  "YESB",             # IFSC bank prefix, when the layout carries one
          "shape": "upi/name/bank/ref/note"
        }
    """
    parsed = parse_description(raw, noise_keywords)
    if not parsed.is_upi:
        return None
    return json.dumps({
        "vpa": parsed.vpa,
        "ref": parsed.ref,
        "note": parsed.note,
        "bank": parsed.bank_code,
        "shape": parsed.shape,
    })
