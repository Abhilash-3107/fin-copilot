"""UPI metadata for storage in transactions.upi_meta.

The one serializer of a ParsedDescription into upi_meta, used at ingest and by
the startup re-derivation alike, so re-deriving a row equals re-ingesting it.
"""
from __future__ import annotations

import json

from finance_copilot.parsers.description import ParsedDescription, parse_description


def upi_meta_json(parsed: ParsedDescription) -> str | None:
    """JSON for transactions.upi_meta, or None for non-UPI lines.

        {"vpa": ..., "ref": ..., "note": ..., "bank": ..., "shape": ...}
    note is the cleaned note (boilerplate, the VPA and the ref removed).
    """
    if not parsed.is_upi:
        return None
    return json.dumps({
        "vpa": parsed.vpa,
        "ref": parsed.ref,
        "note": parsed.note,
        "bank": parsed.bank_code,
        "shape": parsed.shape,
    })


def parse_upi_description(raw: str, noise_keywords: list[str]) -> str | None:
    return upi_meta_json(parse_description(raw, noise_keywords))
