"""The one grammar for bank transaction descriptions.

Every consumer that needs structure out of a raw description (UPI metadata,
counterparty identity, embed text) reads a ParsedDescription from here instead
of splitting the string itself. Banks change their layouts (Kotak inserted an
IFSC bank prefix into UPI lines in June 2026 and shortened the payee field by a
character); with one parser that is one fix, plus a version bump that makes the
app re-derive everything stored from the old parse (see pipeline/derived.py).

Parsing is by token class, not position:
    UPI/<counterparty>[/<qualifier>...]/<ref>[/<note...>] [(Value Date: ...)]
  - the counterparty is always the first token after the rail head,
  - the reference is the first later run of 6+ digits,
  - tokens between counterparty and reference are qualifiers; a 4-letter
    uppercase token there is the IFSC bank prefix,
  - everything after the reference is the note (re-joined: notes may hold '/').

Non-UPI rails (NEFT/IMPS/POS/ATM/...) are returned as rail "other" with no
structure: stripping their refs and dates was evaluated (E5, 2026-07-02) and
rejected for retrieval, so they stay verbatim.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Bump whenever parse output for an existing description can change. Stored in
# app_settings; a mismatch on startup re-derives upi_meta and counterparty_key
# for every stored transaction (pipeline/derived.py).
DESCRIPTION_PARSER_VERSION = 2

SHAPE_UNRECOGNIZED = "unrecognized"

_RAIL_HEADS = {"UPI": "upi", "REV-UPI": "upi_reversal"}

_REF_RE = re.compile(r"^\d{6,}$")
# IFSC bank prefix, e.g. YESB, HDFC, AIRP, SBIN.
_BANK_RE = re.compile(r"^[A-Z]{4}$")
# name@provider with a real provider handle (2+ letters): rejects order ids like
# 'Oid3192692657@A' that merely contain an '@'.
_VPA_RE = re.compile(r"^[\w.\-]{2,}@[A-Za-z]{2,}\w*$")
_VALUE_DATE_RE = re.compile(r"\s*\(\s*Value Date:\s*([^)]*)\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedDescription:
    rail: str                   # "upi", "upi_reversal", "other"
    shape: str                  # e.g. "upi/name/ref/note", "upi/name/bank/ref/note", "unrecognized"
    counterparty: str | None    # payee as printed, whitespace-collapsed (case preserved)
    bank_code: str | None       # IFSC prefix between payee and reference
    ref: str | None             # UPI reference number
    vpa: str | None             # counterparty VPA, when a token is one
    note_raw: str | None        # text after the reference, value-date trailer removed
    note: str | None            # note_raw unless it is boilerplate noise, the VPA, or the ref
    value_date: str | None      # from a trailing "(Value Date: dd-Mon-yy)"

    @property
    def is_upi(self) -> bool:
        return self.rail in ("upi", "upi_reversal")


def _collapse(text: str) -> str:
    return " ".join(text.split())


_OTHER = ParsedDescription(
    rail="other", shape="other", counterparty=None, bank_code=None, ref=None,
    vpa=None, note_raw=None, note=None, value_date=None,
)


def parse_description(raw: str | None, noise_keywords: Iterable[str] = ()) -> ParsedDescription:
    """Parse a raw bank description into its structured parts. Never raises."""
    text = (raw or "").strip()
    value_date = None
    m = _VALUE_DATE_RE.search(text)
    if m:
        value_date = m.group(1).strip() or None
        text = text[: m.start()].rstrip()

    tokens = text.split("/")
    rail = _RAIL_HEADS.get(tokens[0].strip().upper()) if tokens else None
    if rail is None or len(tokens) < 2:
        return _OTHER

    counterparty = _collapse(tokens[1]) or None
    rest = [t.strip() for t in tokens[2:]]

    ref_idx = next((i for i, t in enumerate(rest) if _REF_RE.match(t)), None)
    # The payee token itself can be the VPA (UPI/name@bank/...).
    vpa = next((t for t in [counterparty or "", *rest] if _VPA_RE.match(t)), None)

    if ref_idx is None:
        note_raw = "/".join(t for t in rest if t) or None
        return ParsedDescription(
            rail=rail, shape=SHAPE_UNRECOGNIZED, counterparty=counterparty, bank_code=None,
            ref=None, vpa=vpa, note_raw=note_raw,
            note=_clean_note(note_raw, noise_keywords, vpa, None), value_date=value_date,
        )

    qualifiers = [t for t in rest[:ref_idx] if t]
    ref = rest[ref_idx]
    note_raw = "/".join(rest[ref_idx + 1 :]).strip() or None

    bank_code = None
    head = "upi" if rail == "upi" else "rev-upi"
    if not qualifiers:
        shape = f"{head}/name/ref/note"
    elif len(qualifiers) == 1 and _BANK_RE.match(qualifiers[0]):
        bank_code = qualifiers[0]
        shape = f"{head}/name/bank/ref/note"
    else:
        # Parsed (payee, ref, note are all still found) but a layout we haven't
        # seen: flagged so ingest can report it.
        shape = f"{head}/name/?/ref/note"

    return ParsedDescription(
        rail=rail, shape=shape, counterparty=counterparty, bank_code=bank_code, ref=ref,
        vpa=vpa, note_raw=note_raw, note=_clean_note(note_raw, noise_keywords, vpa, ref),
        value_date=value_date,
    )


def _clean_note(
    note_raw: str | None, noise_keywords: Iterable[str], vpa: str | None, ref: str | None
) -> str | None:
    if not note_raw:
        return None
    noise = {k.upper() for k in noise_keywords}
    if note_raw.upper() in noise or note_raw == vpa or note_raw == ref:
        return None
    return note_raw


def is_known_shape(parsed: ParsedDescription) -> bool:
    """Whether a UPI description matched a layout the grammar fully understands."""
    return parsed.shape in (
        "upi/name/ref/note", "upi/name/bank/ref/note",
        "rev-upi/name/ref/note", "rev-upi/name/bank/ref/note",
    )
