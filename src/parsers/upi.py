"""Shared UPI description parser — bank-agnostic, config-driven noise filtering."""
from __future__ import annotations

import json
import re

# Counterparty VPA, e.g. merchant@okaxis / rahul.k-1@ybl. The domain part is
# alphabetic-leading; this is the strongest categorization signal in UPI strings.
_VPA_RE = re.compile(r"^[\w.\-]{2,}@[A-Za-z][\w]*$")

# UPI transaction reference numbers are long digit runs (typically 12 digits)
_REF_RE = re.compile(r"^\d{6,}$")


def parse_upi_description(raw: str, noise_keywords: list[str]) -> str | None:
    """Return a JSON string of UPI metadata if raw starts with 'UPI/', else None.

    Structure:
        {
          "vpa":  "merchant@okaxis",  # counterparty VPA if any segment looks like one
          "ref":  "118030236405",     # first long digit run (UPI reference)
          "note": "some free text"    # last segment, None if it's a noise keyword
        }
    """
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if not parts or parts[0].upper() != "UPI":
        return None

    segments = parts[1:]

    vpa = next((s for s in segments if _VPA_RE.match(s)), None)
    ref = next((s for s in segments if _REF_RE.match(s)), None)

    # Need at least one middle segment + one last segment to have a note
    note = None
    if len(segments) >= 2:
        last = segments[-1]
        noise_upper = {k.upper() for k in noise_keywords}
        if last.upper() not in noise_upper and last != vpa and last != ref:
            note = last

    return json.dumps({"vpa": vpa, "ref": ref, "note": note})
