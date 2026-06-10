"""CRUD helpers for transaction_groups and transaction_group_members."""
from __future__ import annotations

import sqlite3

import ulid

from src.db.queries.common import dump_string_list, parse_string_list


def _with_parsed_lists(row: dict, fields: tuple[str, ...]) -> dict:
    for field in fields:
        if field in row:
            row[field] = parse_string_list(row[field])
    return row


def create_group(
    conn: sqlite3.Connection,
    name: str,
    note: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    group_id = str(ulid.ULID())
    labels_str = dump_string_list(labels) if labels else None
    conn.execute(
        "INSERT INTO transaction_groups (id, name, note, labels) VALUES (?, ?, ?, ?)",
        (group_id, name, note, labels_str),
    )
    return {"id": group_id, "name": name, "note": note, "labels": labels or []}


def get_group(conn: sqlite3.Connection, group_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM transaction_groups WHERE id = ?", (group_id,)
    ).fetchone()
    return _with_parsed_lists(dict(row), ("labels",)) if row else None


def list_groups(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM transaction_groups ORDER BY created_at DESC"
    ).fetchall()
    return [_with_parsed_lists(dict(r), ("labels",)) for r in rows]


def search_groups(conn: sqlite3.Connection, query: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM transaction_groups WHERE name LIKE ? ORDER BY created_at DESC LIMIT 20",
        (f"%{query}%",),
    ).fetchall()
    return [_with_parsed_lists(dict(r), ("labels",)) for r in rows]


def delete_group(conn: sqlite3.Connection, group_id: str) -> bool:
    cursor = conn.execute("DELETE FROM transaction_groups WHERE id = ?", (group_id,))
    return cursor.rowcount > 0


def add_member(
    conn: sqlite3.Connection,
    group_id: str,
    transaction_id: str,
    role: str | None = None,
    people: list[str] | None = None,
    labels: list[str] | None = None,
    txn_type: str | None = None,
) -> None:
    people_str = dump_string_list(people) if people else None
    labels_str = dump_string_list(labels) if labels else None
    conn.execute(
        "INSERT OR IGNORE INTO transaction_group_members (group_id, transaction_id, role, people, labels, txn_type) VALUES (?, ?, ?, ?, ?, ?)",
        (group_id, transaction_id, role, people_str, labels_str, txn_type),
    )


def update_member(
    conn: sqlite3.Connection,
    group_id: str,
    transaction_id: str,
    people: list[str] | None = None,
    labels: list[str] | None = None,
    txn_type: str | None = None,
) -> bool:
    """Update people, labels, and/or txn_type on a membership. Only updates fields that are explicitly passed."""
    clauses = []
    params: list = []
    if people is not None:
        clauses.append("people = ?")
        params.append(dump_string_list(people) if people else None)
    if labels is not None:
        clauses.append("labels = ?")
        params.append(dump_string_list(labels) if labels else None)
    if txn_type is not None:
        clauses.append("txn_type = ?")
        params.append(txn_type)
    if not clauses:
        return False
    params.extend([group_id, transaction_id])
    cursor = conn.execute(
        f"UPDATE transaction_group_members SET {', '.join(clauses)} WHERE group_id = ? AND transaction_id = ?",
        params,
    )
    return cursor.rowcount > 0


def remove_member(conn: sqlite3.Connection, group_id: str, transaction_id: str) -> bool:
    cursor = conn.execute(
        "DELETE FROM transaction_group_members WHERE group_id = ? AND transaction_id = ?",
        (group_id, transaction_id),
    )
    return cursor.rowcount > 0


def list_groups_for_transaction(conn: sqlite3.Connection, transaction_id: str) -> list[dict]:
    """Return all groups a transaction belongs to, with member summary."""
    rows = conn.execute(
        """
        SELECT g.id, g.name, g.note, g.created_at, m.role, m.txn_type, m.people, m.labels,
               (SELECT COUNT(*) FROM transaction_group_members WHERE group_id = g.id) AS member_count
        FROM transaction_groups g
        JOIN transaction_group_members m ON m.group_id = g.id
        WHERE m.transaction_id = ?
        ORDER BY g.created_at DESC
        """,
        (transaction_id,),
    ).fetchall()
    return [_with_parsed_lists(dict(r), ("people", "labels")) for r in rows]


def list_members_for_group(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """Return all transactions in a group with their basic fields."""
    rows = conn.execute(
        """
        SELECT m.role, m.txn_type, m.transaction_id, t.id, t.txn_date, t.amount, t.debit_credit, t.raw_description, t.upi_meta
        FROM transaction_group_members m
        JOIN transactions t ON t.id = m.transaction_id
        WHERE m.group_id = ?
        ORDER BY t.txn_date, t.id
        """,
        (group_id,),
    ).fetchall()
    return [dict(r) for r in rows]
