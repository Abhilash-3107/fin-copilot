"""The name a payment goes by: one resolution for every API that shows one.

Resolution order, first that exists wins:

  1. the payment's own name         transactions.display_name (the user)
  2. the payee's name               payee_names, by counterparty_key (the user)
  3. the annotation's merchant      what sorting, or the user in the label form, set
  4. the payee as the bank printed  parsed out of the line, tidied
  5. the printed line itself        when nothing could be parsed

A name is a label. Renaming never changes raw_description, counterparty_key,
categories, learned rules or matching, and insights group by key, so a rename
changes what a row is called and nothing else.

Rows are transactions as the query helpers return them (display_name,
counterparty_key, raw_description, upi_meta), with the annotation's merchant
when the query joined it; with_payee looks it up when it did not and adds the
display fields in place.
"""
from __future__ import annotations

import json
import sqlite3

from finance_copilot.parsers.description import SHAPE_UNRECOGNIZED, channel_of, parse_description

# Long enough for any real name, short enough that a paste of a whole bank
# line is a mistake the form can refuse.
MAX_NAME_LENGTH = 80


def load_payee_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT counterparty_key, name FROM payee_names")}


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _tidy(printed: str) -> str:
    """A payee as the bank prints it, made readable: a UPI handle in lower
    case, a name printed in capitals in title case, anything else as is."""
    if "@" in printed:
        return printed.lower()
    return printed.title() if printed.isupper() else printed


def printed_payee(raw_description: str | None) -> str:
    """Levels 4 and 5: the payee parsed out of the line, else the line itself."""
    raw = (raw_description or "").strip()
    parsed = parse_description(raw)
    if parsed.counterparty and parsed.shape != SHAPE_UNRECOGNIZED:
        return _tidy(parsed.counterparty)
    return raw


def set_name(
    display_name: str | None, counterparty_key: str | None, merchant: str | None,
    payee_names: dict[str, str], *, own: bool = True,
) -> str | None:
    """Levels 1 to 3: a name somebody set. `own=False` leaves out the payment's
    own name, giving what every payment to the same payee is called."""
    return (
        (_clean(display_name) if own else None)
        or payee_names.get(counterparty_key or "")
        or _clean(merchant)
    )


def resolve_payee(row: dict, payee_names: dict[str, str], *, own: bool = True) -> str:
    return (
        set_name(row.get("display_name"), row.get("counterparty_key"), row.get("merchant"), payee_names, own=own)
        or printed_payee(row.get("raw_description"))
    )


def _note(upi_meta) -> str | None:
    if not upi_meta:
        return None
    try:
        meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
    except json.JSONDecodeError:
        return None
    return _clean(meta.get("note")) if isinstance(meta, dict) else None


def _id_of(row: dict) -> str:
    return row["id"] if "id" in row else row["transaction_id"]


def _merchants_of(conn: sqlite3.Connection, transaction_ids: list[str]) -> dict[str, str | None]:
    """Annotation merchants for rows that were listed without their annotation."""
    merchants: dict[str, str | None] = {}
    for start in range(0, len(transaction_ids), 500):
        chunk = transaction_ids[start:start + 500]
        for r in conn.execute(
            f"SELECT transaction_id, merchant FROM annotations WHERE transaction_id IN ({','.join('?' * len(chunk))})",
            chunk,
        ):
            merchants[r[0]] = r[1]
    return merchants


def with_payee(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Add what a row needs to say who it was to, in place, and return the rows.

      payee       the resolved name
      payee_all   what every payment to this payee is called (no payment name);
                  None for a payment with no payee key, where "every payment" means nothing
      payee_name  the name the user gave the payee, if any
      note        the user's UPI note
      channel     UPI, NEFT, Card ...; None when the line does not say
    """
    names = load_payee_names(conn)
    merchants = _merchants_of(conn, [_id_of(r) for r in rows if "merchant" not in r])
    for row in rows:
        key = row.get("counterparty_key")
        merchant = row["merchant"] if "merchant" in row else merchants.get(_id_of(row))
        named = {**row, "merchant": merchant}
        row["payee"] = resolve_payee(named, names)
        row["payee_all"] = resolve_payee(named, names, own=False) if key else None
        row["payee_name"] = names.get(key or "")
        row["note"] = _note(row.get("upi_meta"))
        row["channel"] = channel_of(row.get("raw_description"))
    return rows


def set_payment_name(conn: sqlite3.Connection, transaction_id: str, name: str | None) -> bool:
    """Name one payment (None or blank clears it). False when it does not exist."""
    cur = conn.execute(
        "UPDATE transactions SET display_name = ? WHERE id = ?", (_clean(name), transaction_id)
    )
    return cur.rowcount > 0


def set_payee_name(conn: sqlite3.Connection, counterparty_key: str, name: str | None) -> None:
    """Name every payment to a payee, past and future (None or blank clears it)."""
    name = _clean(name)
    if name is None:
        conn.execute("DELETE FROM payee_names WHERE counterparty_key = ?", (counterparty_key,))
        return
    conn.execute(
        "INSERT INTO payee_names (counterparty_key, name) VALUES (?, ?) "
        "ON CONFLICT(counterparty_key) DO UPDATE SET name = excluded.name, updated_at = datetime('now')",
        (counterparty_key, name),
    )
