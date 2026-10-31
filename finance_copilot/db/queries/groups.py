"""CRUD helpers for transaction_groups and transaction_group_members."""
from __future__ import annotations

import sqlite3

import ulid

from finance_copilot.db.queries.common import dump_string_list, parse_string_list


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
    role: str | None = None,
) -> bool:
    """Update people, labels, txn_type, and/or role on a membership. Only updates
    fields that are explicitly passed. An empty string clears role/txn_type."""
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
        params.append(txn_type or None)
    if role is not None:
        clauses.append("role = ?")
        params.append(role or None)
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
        SELECT m.role, m.txn_type, m.people, m.transaction_id, t.id, t.txn_date, t.amount, t.debit_credit, t.raw_description, t.upi_meta
        FROM transaction_group_members m
        JOIN transactions t ON t.id = m.transaction_id
        WHERE m.group_id = ?
        ORDER BY t.txn_date, t.id
        """,
        (group_id,),
    ).fetchall()
    return [_with_parsed_lists(dict(r), ("people",)) for r in rows]


def summarize_groups(conn: sqlite3.Connection, group_ids: list[str]) -> dict[str, dict]:
    """Per-group rollup for the collapsed row: date range, spend/received totals,
    net position, and member count. One query over all requested groups.

    net = credits - debits across the group's transactions, i.e. money that came
    to the user minus money that left. spend/received are the gross legs.
    """
    if not group_ids:
        return {}
    placeholders = ",".join("?" * len(group_ids))
    rows = conn.execute(
        f"""
        SELECT m.group_id,
               COUNT(*)                                                   AS member_count,
               MIN(t.txn_date)                                            AS first_date,
               MAX(t.txn_date)                                            AS last_date,
               COALESCE(SUM(CASE WHEN t.debit_credit = 'debit'  THEN t.amount ELSE 0 END), 0) AS spend,
               COALESCE(SUM(CASE WHEN t.debit_credit = 'credit' THEN t.amount ELSE 0 END), 0) AS received
        FROM transaction_group_members m
        JOIN transactions t ON t.id = m.transaction_id
        WHERE m.group_id IN ({placeholders})
        GROUP BY m.group_id
        """,
        group_ids,
    ).fetchall()
    summary: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["net"] = round(d["received"] - d["spend"], 2)
        summary[d.pop("group_id")] = d
    return summary


def settle_up_for_group(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """Per-person settle-up over a group's members.

    A person's `net` is what they owe you minus what you owe them; positive means
    they still owe you. With n people tagged on a member transaction:

        credit (any type)                     -amount/n      they paid you back
        debit  split / event / untyped        +amount/(n+1)  shared cost you paid;
                                                             you are a sharer too
        debit  transfer / refund              +amount/n      money you sent them
        debit  reimbursement                  no effect      you repaid something
                                                             they covered off-ledger

    Every credit counts in full because money a tagged person sends you inside
    an expense group is their share coming back, whatever it was labelled
    (insights' group offsets read it the same way). Equal-splitting it would
    halve every repayment. A reimbursement debit settles a debt this ledger
    never saw, so counting it either way would invent a balance. `role` is
    deliberately not read.

    `people` holds person ids; `name` resolves them, falling back to the raw
    value for tags that predate the people table.
    """
    balances: dict[str, float] = {}
    for m in list_members_for_group(conn, group_id):
        people = m.get("people") or []
        txn_type = m.get("txn_type")
        if not people or (m["debit_credit"] == "debit" and txn_type == "reimbursement"):
            continue
        if m["debit_credit"] == "credit":
            delta = -m["amount"] / len(people)
        elif txn_type in ("transfer", "refund"):
            delta = m["amount"] / len(people)
        else:
            delta = m["amount"] / (len(people) + 1)
        delta = round(delta, 2)
        for person in people:
            balances[person] = round(balances.get(person, 0.0) + delta, 2)
    names = _person_names(conn, list(balances))
    return [
        {"person": person, "name": names.get(person, person), "net": net}
        for person, net in sorted(balances.items(), key=lambda kv: -abs(kv[1]))
        if abs(net) >= 0.01
    ]


def _person_names(conn: sqlite3.Connection, person_ids: list[str]) -> dict[str, str]:
    if not person_ids:
        return {}
    placeholders = ",".join("?" * len(person_ids))
    rows = conn.execute(
        f"SELECT id, name FROM people WHERE id IN ({placeholders})", person_ids
    ).fetchall()
    return {r[0]: r[1] for r in rows}
