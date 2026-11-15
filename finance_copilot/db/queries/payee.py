"""The name a payment goes by: one resolution for every API that shows one.

Resolution order, first that exists wins:

  1. the payment's own name         transactions.display_name (the user)
  2. the payee's name               payee_names, by counterparty_key (the user)
  3. the annotation's merchant      what sorting set on this payment
  4. the payee's merchant           the most common merchant among the payee's
                                    sorted payments, so a payment sorted by a
                                    rule (no merchant) reads like its siblings
  5. the payee as the bank printed  parsed out of the line, as printed
  6. the printed line itself        when nothing could be parsed, without the
                                    channel word the row already shows

A name is a label. Renaming never changes raw_description, counterparty_key,
categories, learned rules or matching, and insights group by key, so a rename
changes what a row is called and nothing else.

Levels 2, 4 and 5 are the payee's name, without any one payment's: payee_labels
is the one place they are worked out, for the Ledger (payee_all) and insights
alike, so the same payee is never called two things.

Rows are transactions as the query helpers return them (display_name,
counterparty_key, raw_description, upi_meta), with the annotation's merchant
when the query joined it; with_payee looks it up when it did not and adds the
display fields in place.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from finance_copilot.config import settings
from finance_copilot.parsers.description import (
    SHAPE_UNRECOGNIZED,
    channel_of,
    parse_description,
    without_channel,
)

# Long enough for any real name, short enough that a paste of a whole bank
# line is a mistake the form can refuse.
MAX_NAME_LENGTH = 80


def load_payee_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT counterparty_key, name FROM payee_names")}


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def printed_payee(raw_description: str | None) -> str:
    """Levels 5 and 6: the payee parsed out of the line as the bank printed it
    (a UPI handle in lower case), else the line itself without its channel."""
    raw = (raw_description or "").strip()
    parsed = parse_description(raw)
    if parsed.counterparty and parsed.shape != SHAPE_UNRECOGNIZED:
        return parsed.counterparty.lower() if "@" in parsed.counterparty else parsed.counterparty
    return without_channel(raw) if channel_of(raw) else raw


def _chunks(items: list, size: int = 500):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def payee_labels(conn: sqlite3.Connection, keys: list[str] | None = None) -> dict[str, str]:
    """What every payment to a payee is called, by counterparty_key: the name
    the user gave it, else its most common merchant (ties to the first in
    alphabetical order, so it never flips between loads), else the payee as
    the bank printed it on its latest payment. `keys` limits the work to the
    payees a page shows; None means all of them.
    """
    names = load_payee_names(conn)
    labels: dict[str, str] = {}
    for chunk in ([None] if keys is None else _chunks(sorted(set(keys)))):
        where = "" if chunk is None else f" AND t.counterparty_key IN ({','.join('?' * len(chunk))})"
        args = [] if chunk is None else chunk
        merchants: dict[str, Counter] = {}
        for r in conn.execute(
            "SELECT t.counterparty_key, a.merchant, COUNT(*) FROM transactions t "
            "JOIN annotations a ON a.transaction_id = t.id "
            f"WHERE t.counterparty_key IS NOT NULL AND TRIM(COALESCE(a.merchant, '')) != ''{where} "
            "GROUP BY t.counterparty_key, a.merchant", args,
        ):
            merchants.setdefault(r[0], Counter())[r[1].strip()] += r[2]
        for key, counts in merchants.items():
            labels[key] = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        for r in conn.execute(
            "SELECT counterparty_key, raw_description FROM ("
            "  SELECT t.counterparty_key, t.raw_description, ROW_NUMBER() OVER ("
            "    PARTITION BY t.counterparty_key ORDER BY t.txn_date DESC, t.id DESC) AS n "
            f"  FROM transactions t WHERE t.counterparty_key IS NOT NULL{where}"
            ") WHERE n = 1", args,
        ):
            if r[0] not in labels:
                labels[r[0]] = printed_payee(r[1])
    # The user's own name for the payee outranks everything computed.
    return {key: names.get(key, label) for key, label in labels.items()}


_QUALIFIER = re.compile(r"^[A-Z0-9 .&@'-]+$")


def clean_note(note: str | None) -> str | None:
    """The part of a stored UPI note a person typed, for display.

    Notes arrive as "/"-separated segments, and banks and payment apps add
    their own: all-capitals or numeric qualifiers (AUTOPAY, a bank code, an
    order number) and fixed app texts (settings.upi_note_app_texts). Those
    segments are dropped; what is left is joined, or None when nothing is.
    The stored note, search and embeddings are not touched.
    """
    prefixes = tuple(p.lower() for p in settings.upi_note_app_texts)
    kept = [
        seg for seg in (s.strip() for s in (note or "").split("/"))
        if seg and not _QUALIFIER.match(seg) and not seg.lower().startswith(prefixes)
    ]
    return "/".join(kept) or None


def _note(upi_meta) -> str | None:
    if not upi_meta:
        return None
    try:
        meta = json.loads(upi_meta) if isinstance(upi_meta, str) else upi_meta
    except json.JSONDecodeError:
        return None
    return clean_note(meta.get("note")) if isinstance(meta, dict) else None


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
    labels = payee_labels(conn, [r["counterparty_key"] for r in rows if r.get("counterparty_key")])
    merchants = _merchants_of(conn, [_id_of(r) for r in rows if "merchant" not in r])
    for row in rows:
        key = row.get("counterparty_key")
        merchant = row["merchant"] if "merchant" in row else merchants.get(_id_of(row))
        row["payee"] = (
            _clean(row.get("display_name"))
            or names.get(key or "")
            or _clean(merchant)
            or labels.get(key or "")
            or printed_payee(row.get("raw_description"))
        )
        row["payee_all"] = labels.get(key) if key else None
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
