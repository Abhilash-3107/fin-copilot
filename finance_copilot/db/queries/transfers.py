"""A transfer between the user's own accounts: the facts pairing writes, and how to take them back.

A pair is one `transaction_links` row of type 'transfer' (a debit in one account,
the matching credit in another) plus a Self Transfers annotation with source
'pair' on each side. There is no second mechanism: In, Out and every breakdown
already leave Self Transfers out (db/queries/insights.py), so a pair needs no
rule of its own there, and `_spend_offsets` reads only split, reimbursement and
refund links, never this one.

Which rows pair, and when, is pipeline/pairing.py. This module is the storage:
write a pair, take one apart, release the pairs of rows that are going away, and
say on a payment what it was paired with. The user taking a pair apart is
remembered (transfer_unpaired) so pairing never puts it back.
"""
from __future__ import annotations

import sqlite3

import ulid

from finance_copilot.db.queries.accounts import account_labels, label_for_statement
from finance_copilot.db.queries.categories import resolve_category_ids
from finance_copilot.pipeline.sources import VERIFIED_SOURCES

PAIR_SOURCE = "pair"
LINK_TYPE = "transfer"
SELF_CATEGORY = "Self Transfers"
SELF_SUBCATEGORY = "Self Transfer"

_CHUNK = 400


def _chunks(items: list[str]):
    for start in range(0, len(items), _CHUNK):
        yield items[start:start + _CHUNK]


def _ordered(a: str, b: str) -> tuple[str, str]:
    """The pair as the table stores it (txn_a < txn_b)."""
    return (a, b) if a < b else (b, a)


def transfer_link(conn: sqlite3.Connection, transaction_id: str) -> sqlite3.Row | None:
    """The transfer link a payment is in, if any."""
    return conn.execute(
        "SELECT * FROM transaction_links WHERE link_type = ? AND (txn_a = ? OR txn_b = ?)",
        (LINK_TYPE, transaction_id, transaction_id),
    ).fetchone()


def _write_pair_annotation(conn: sqlite3.Connection, transaction_id: str) -> None:
    """Sort one side as Self Transfers with source 'pair'. A label the user wrote
    or imported as Self Transfers stays as it is (their word on it); any other
    label is replaced, whoever made it. Pairing never reaches a hand label of
    another category: the rule filters those rows out before it gets here."""
    existing = conn.execute(
        "SELECT id, source, category FROM annotations WHERE transaction_id = ?", (transaction_id,)
    ).fetchone()
    category_id, subcategory_id = resolve_category_ids(conn, SELF_CATEGORY, SELF_SUBCATEGORY)
    if existing is None:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, merchant, category, subcategory, tags, confidence, "
            "source, category_id, subcategory_id) VALUES (?, ?, NULL, ?, ?, '[]', 1.0, ?, ?, ?)",
            (str(ulid.ULID()), transaction_id, SELF_CATEGORY,
             SELF_SUBCATEGORY, PAIR_SOURCE, category_id, subcategory_id),
        )
    elif existing["source"] in VERIFIED_SOURCES and existing["category"] == SELF_CATEGORY:
        return
    else:
        conn.execute(
            "UPDATE annotations SET merchant = NULL, category = ?, subcategory = ?, tags = '[]', "
            "confidence = 1.0, source = ?, category_id = ?, subcategory_id = ?, reasoning = NULL, "
            "annotated_at = datetime('now') WHERE id = ?",
            (SELF_CATEGORY, SELF_SUBCATEGORY, PAIR_SOURCE, category_id, subcategory_id, existing["id"]),
        )
    # A skipped payment that now has an answer is a new question, as for any new label.
    conn.execute("UPDATE transactions SET review_snoozed_at = NULL WHERE id = ?", (transaction_id,))


def link_pair(conn: sqlite3.Connection, debit_id: str, credit_id: str) -> None:
    """Pair a debit with a credit: the link, and Self Transfers on both sides.
    A pair the user once took apart is allowed again by calling this: choosing it
    is a new decision. The caller commits, and has checked the rule."""
    low, high = _ordered(debit_id, credit_id)
    conn.execute(
        "INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES (?, ?, ?, ?)",
        (f"lnk_{ulid.ULID()}", low, high, LINK_TYPE),
    )
    conn.execute("DELETE FROM transfer_unpaired WHERE txn_a = ? AND txn_b = ?", (low, high))
    for transaction_id in (debit_id, credit_id):
        _write_pair_annotation(conn, transaction_id)


def _drop_pair_annotations(conn: sqlite3.Connection, transaction_ids: list[str]) -> None:
    """Remove the 'pair' annotations on these payments, so they are To sort again.
    One the user has since edited (source manual) is theirs and stays."""
    for chunk in _chunks(transaction_ids):
        conn.execute(
            f"DELETE FROM annotations WHERE source = ? AND transaction_id IN ({','.join('?' * len(chunk))})",
            (PAIR_SOURCE, *chunk),
        )


def unpair(conn: sqlite3.Connection, transaction_id: str, *, remember: bool = True) -> list[str]:
    """Take this payment's pair apart: the link goes, both 'pair' annotations go (the
    two payments return to To sort), and with `remember` the pair is recorded so
    pairing does not make it again. Returns the two payment ids, [] when it was
    not paired. The caller commits."""
    link = transfer_link(conn, transaction_id)
    if link is None:
        return []
    ids = [link["txn_a"], link["txn_b"]]
    conn.execute("DELETE FROM transaction_links WHERE id = ?", (link["id"],))
    _drop_pair_annotations(conn, ids)
    if remember:
        conn.execute("INSERT OR IGNORE INTO transfer_unpaired (txn_a, txn_b) VALUES (?, ?)", (ids[0], ids[1]))
    return ids


def release_transfers(conn: sqlite3.Connection, transaction_ids: list[str], *, drop_decisions: bool) -> list[str]:
    """Free the pairs of payments that are about to be deleted or started over: each
    pair's link and both 'pair' annotations go, so the partner in another statement
    returns to To sort instead of staying Self Transfers with nobody on the other
    end. With `drop_decisions` the user's recorded unpairings of these payments
    go too (the payments themselves are going; Start over keeps them).
    Returns the partner payments outside `transaction_ids`. The caller commits."""
    ids = list(dict.fromkeys(transaction_ids))
    inside = set(ids)
    partners: list[str] = []
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for link in conn.execute(
            f"SELECT * FROM transaction_links WHERE link_type = ? AND (txn_a IN ({marks}) OR txn_b IN ({marks}))",
            (LINK_TYPE, *chunk, *chunk),
        ).fetchall():
            pair = [link["txn_a"], link["txn_b"]]
            conn.execute("DELETE FROM transaction_links WHERE id = ?", (link["id"],))
            _drop_pair_annotations(conn, pair)
            partners.extend(t for t in pair if t not in inside)
        if drop_decisions:
            conn.execute(
                f"DELETE FROM transfer_unpaired WHERE txn_a IN ({marks}) OR txn_b IN ({marks})", (*chunk, *chunk)
            )
    return list(dict.fromkeys(partners))


def unpair_same_account(conn: sqlite3.Connection) -> list[str]:
    """Take apart every transfer whose two payments now sit in one account (the
    user moved a statement): money does not transfer within an account. Not
    remembered as a decision of the user's. Returns the payments freed."""
    freed: list[str] = []
    for link in conn.execute(
        """
        SELECT l.txn_a FROM transaction_links l
        JOIN transactions a ON a.id = l.txn_a JOIN statements sa ON sa.id = a.statement_id
        JOIN transactions b ON b.id = l.txn_b JOIN statements sb ON sb.id = b.statement_id
        WHERE l.link_type = ? AND sa.account_id IS sb.account_id
        """,
        (LINK_TYPE,),
    ).fetchall():
        freed.extend(unpair(conn, link["txn_a"], remember=False))
    return freed


def with_transfer(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Add `transfer` to payments, in place: None for a payment that is not paired,
    else what it is paired with, in words the page prints as they are.

      transaction_id  the payment on the other side
      direction       "to" on the debit, "from" on the credit
      account_label   the other account's label
      txn_date        the other payment's date
      text            "Moved to SBI savings" / "Moved from Kotak savings"
    """
    for row in rows:
        row["transfer"] = None
    by_id = {(r["id"] if "id" in r else r["transaction_id"]): r for r in rows}
    if not by_id:
        return rows
    labels = account_labels(conn)
    for chunk in _chunks(list(by_id)):
        marks = ",".join("?" * len(chunk))
        for link in conn.execute(
            f"""
            SELECT l.txn_a, l.txn_b,
                   a.txn_date AS date_a, a.debit_credit AS dc_a,
                   sa.account_id AS account_a, sa.bank_name AS bank_a, sa.account_ref AS ref_a,
                   b.txn_date AS date_b, b.debit_credit AS dc_b,
                   sb.account_id AS account_b, sb.bank_name AS bank_b, sb.account_ref AS ref_b
            FROM transaction_links l
            JOIN transactions a ON a.id = l.txn_a JOIN statements sa ON sa.id = a.statement_id
            JOIN transactions b ON b.id = l.txn_b JOIN statements sb ON sb.id = b.statement_id
            WHERE l.link_type = ? AND (l.txn_a IN ({marks}) OR l.txn_b IN ({marks}))
            """,
            (LINK_TYPE, *chunk, *chunk),
        ):
            for me, other in (("a", "b"), ("b", "a")):
                row = by_id.get(link[f"txn_{me}"])
                if row is None:
                    continue
                label = label_for_statement(labels, {
                    "account_id": link[f"account_{other}"],
                    "bank_name": link[f"bank_{other}"],
                    "account_ref": link[f"ref_{other}"],
                })
                direction = "to" if link[f"dc_{me}"] == "debit" else "from"
                row["transfer"] = {
                    "transaction_id": link[f"txn_{other}"],
                    "direction": direction,
                    "account_label": label,
                    "txn_date": link[f"date_{other}"],
                    "text": f"Moved {direction} {label}",
                }
    return rows
