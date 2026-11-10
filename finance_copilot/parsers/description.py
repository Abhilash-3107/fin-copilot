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
    token right before the reference is the IFSC bank prefix (Kotak sometimes
    prints it lowercase: "utib"),
  - any other qualifiers are the rest of a payee name that contains '/'
    ("UPI/M/S.HARRISONS /ICIC/<ref>", i.e. "Messrs Harrisons"),
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
# for every stored transaction (pipeline/derived.py). A migration that repairs
# raw descriptions deletes the stored value instead of bumping this.
DESCRIPTION_PARSER_VERSION = 3

SHAPE_UNRECOGNIZED = "unrecognized"

_RAIL_HEADS = {"UPI": "upi", "REV-UPI": "upi_reversal"}

_REF_RE = re.compile(r"^\d{6,}$")
# IFSC bank prefix, e.g. YESB, HDFC, AIRP, SBIN, and lowercase "utib" as Kotak
# sometimes prints it. Only trusted in the slot right before the reference.
_BANK_RE = re.compile(r"^[A-Za-z]{4}$")
# An uppercase bank-like token anywhere else means the layout is ambiguous.
_STRICT_BANK_RE = re.compile(r"^[A-Z]{4}$")
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
    rail = _RAIL_HEADS.get(tokens[0].strip().upper())
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
    if qualifiers and _BANK_RE.match(qualifiers[-1]):
        bank_code = qualifiers[-1].upper()
        qualifiers = qualifiers[:-1]
    if qualifiers and not _is_payee_continuation(qualifiers):
        # Parsed (payee, ref, note are all still found) but a layout we haven't
        # seen: flagged so ingest can report it.
        bank_code = None
        shape = f"{head}/name/?/ref/note"
    else:
        if qualifiers:
            counterparty = _collapse("/".join([tokens[1], *qualifiers]))
        shape = f"{head}/name/bank/ref/note" if bank_code else f"{head}/name/ref/note"

    return ParsedDescription(
        rail=rail, shape=shape, counterparty=counterparty, bank_code=bank_code, ref=ref,
        vpa=vpa, note_raw=note_raw, note=_clean_note(note_raw, noise_keywords, vpa, ref),
        value_date=value_date,
    )


def _is_payee_continuation(tokens: list[str]) -> bool:
    """Whether the tokens read as the rest of a payee name that contained '/'.

    Rejects anything that looks like a structural field (a VPA, a bank code, a
    number), so a genuinely new layout is still reported rather than absorbed.
    """
    return all(
        not _VPA_RE.match(t) and not _STRICT_BANK_RE.match(t) and not any(c.isdigit() for c in t)
        for t in tokens
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


KNOWN_SHAPES = frozenset({
    "upi/name/ref/note", "upi/name/bank/ref/note",
    "rev-upi/name/ref/note", "rev-upi/name/bank/ref/note",
})


def is_known_shape(parsed: ParsedDescription) -> bool:
    """Whether a UPI description matched a layout the grammar fully understands."""
    return parsed.shape in KNOWN_SHAPES
