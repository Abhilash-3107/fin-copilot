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
from finance_copilot.pipeline.sources import USER_LABEL_SOURCES

PAIR_SOURCE = "pair"
LINK_TYPE = "transfer"
SELF_CATEGORY = "Self Transfers"
SELF_SUBCATEGORY = "Self Transfer"

# NEFT and IMPS can land a day or two later, a weekend adds one.
WINDOW_DAYS = 3

_INTENT = ",".join(f"'{s}'" for s in sorted(USER_LABEL_SOURCES))


def _account_of(t: str) -> str:
    return f"(SELECT s.account_id FROM statements s WHERE s.id = {t}.statement_id)"


def _free(t: str) -> str:
    """SQL: payment `t` is in no transfer link and carries no label the user made or
    wrote (manual, imported, user_rule) in a category other than Self Transfers."""
    return (
        f"(NOT EXISTS (SELECT 1 FROM transaction_links l WHERE l.link_type = '{LINK_TYPE}' "
        f"AND (l.txn_a = {t}.id OR l.txn_b = {t}.id)) "
        f"AND NOT EXISTS (SELECT 1 FROM annotations pa WHERE pa.transaction_id = {t}.id "
        f"AND pa.source IN ({_INTENT}) AND pa.category IS NOT '{SELF_CATEGORY}'))"
    )


def fits_sql(x: str, y: str, *, respect_unpaired: bool = True) -> str:
    """The one definition of "these two payments could be the two sides of a transfer
    between the user's own accounts", as a SQL condition over transactions aliased `x`
    and `y` (direction aside: the caller says which is the debit):

      - in different accounts,
      - the same amount to the paisa,
      - at most WINDOW_DAYS apart,
      - neither in a transfer link,
      - neither labelled by the user (manual, imported, user_rule) as anything but Self Transfers,
      - and, unless `respect_unpaired` is False (the user choosing the pair is a new
        decision), not a pair the user took apart before.

    pipeline/pairing.py finds its candidates with this, and `has_open_candidate_sql`
    (so "needs you" in common.unsorted_sql) is built from it: the two cannot disagree.
    The amount test is written as a range first so the index on amount serves it.
    """
    unpaired = (
        f" AND NOT EXISTS (SELECT 1 FROM transfer_unpaired u WHERE u.txn_a = MIN({x}.id, {y}.id) "
        f"AND u.txn_b = MAX({x}.id, {y}.id))"
        if respect_unpaired else ""
    )
    return (
        f"{_account_of(x)} IS NOT NULL AND {_account_of(y)} IS NOT NULL "
        f"AND {_account_of(x)} != {_account_of(y)} "
        f"AND {y}.amount BETWEEN {x}.amount - 0.005 AND {x}.amount + 0.005 "
        f"AND CAST(ROUND({x}.amount * 100) AS INTEGER) = CAST(ROUND({y}.amount * 100) AS INTEGER) "
        f"AND ABS(julianday(date({x}.txn_date)) - julianday(date({y}.txn_date))) <= {WINDOW_DAYS} "
        f"AND {_free(x)} AND {_free(y)}{unpaired}"
    )


def has_open_candidate_sql(t: str = "t") -> str:
    """SQL: payment `t` has at least one payment it could be paired with (fits_sql).
    Pairing leaves it alone when it is not sure, so the payment waits for the user."""
    return (
        f"EXISTS (SELECT 1 FROM transactions o WHERE o.debit_credit != {t}.debit_credit "
        f"AND {fits_sql(t, 'o')})"
    )


def candidate_pairs(
    conn: sqlite3.Connection, *, ids: list[str] | None = None, between: tuple[str, str] | None = None,
    respect_unpaired: bool = True,
) -> list[tuple[str, str, int]]:
    """Every (debit id, credit id, days apart) that fits_sql, closest first. `ids` keeps
    the pairs that involve one of those payments, `between` the pairs of exactly those two."""
    where, params = "", []
    if ids is not None:
        marks = ",".join("?" * len(ids))
        where += f" AND (d.id IN ({marks}) OR c.id IN ({marks}))"
        params += [*ids, *ids]
    if between is not None:
        where += " AND ((d.id = ? AND c.id = ?) OR (d.id = ? AND c.id = ?))"
        params += [between[0], between[1], between[1], between[0]]
    rows = conn.execute(
        f"""
        SELECT d.id AS debit_id, c.id AS credit_id,
               CAST(ABS(julianday(date(d.txn_date)) - julianday(date(c.txn_date))) AS INTEGER) AS gap
        FROM transactions d JOIN transactions c ON c.debit_credit = 'credit'
          AND {fits_sql('d', 'c', respect_unpaired=respect_unpaired)}
        WHERE d.debit_credit = 'debit'{where}
        ORDER BY gap, d.id, c.id
        """,
        params,
    ).fetchall()
    return [(r["debit_id"], r["credit_id"], r["gap"]) for r in rows]

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
    """Sort one side as Self Transfers with source 'pair'. A label the user wrote,
    imported or made a rule for as Self Transfers stays as it is (their word on it); any other
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
    elif existing["source"] in USER_LABEL_SOURCES and existing["category"] == SELF_CATEGORY:
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


def unpair_if_relabelled(conn: sqlite3.Connection, transaction_id: str, category: str | None) -> list[str]:
    """The one rule for a payment that is in a transfer and is given a category: if it
    is anything but Self Transfers the user is saying it is not a transfer between
    their accounts, so the pair goes (the partner returns to To sort) and is remembered.
    Whatever the label's source was: a hand label on a paired side is no exception.
    Every write that sets a category on an existing label calls this
    (annotations.update_annotation). Returns the two payment ids, [] when nothing
    was taken apart. The caller commits."""
    if category in (None, SELF_CATEGORY):
        return []
    return unpair(conn, transaction_id, remember=True)


def unpair(conn: sqlite3.Connection, transaction_id: str, *, remember: bool = True) -> list[str]:
    """Take this payment's pair apart: the link goes, both 'pair' annotations go (the
    two payments return to To sort), and with `remember` the pair is recorded so
    pairing does not make it again. Returns the two payment ids, [] when it was
    not paired. A side the user labelled keeps its label. The caller commits."""
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
