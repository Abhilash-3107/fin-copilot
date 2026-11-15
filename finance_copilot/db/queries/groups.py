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


def _members_for_groups(conn: sqlite3.Connection, group_ids: list[str]) -> dict[str, list[dict]]:
    """Members of every given group in one query, keyed by group id, oldest first."""
    if not group_ids:
        return {}
    placeholders = ",".join("?" * len(group_ids))
    rows = conn.execute(
        f"""
        SELECT m.group_id, m.role, m.txn_type, m.people, m.transaction_id, t.id, t.txn_date, t.amount, t.debit_credit, t.raw_description, t.upi_meta,
               t.counterparty_key, t.display_name, a.merchant
        FROM transaction_group_members m
        JOIN transactions t ON t.id = m.transaction_id
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE m.group_id IN ({placeholders})
        ORDER BY t.txn_date, t.id
        """,
        group_ids,
    ).fetchall()
    by_group: dict[str, list[dict]] = {}
    for r in rows:
        member = _with_parsed_lists(dict(r), ("people",))
        by_group.setdefault(member.pop("group_id"), []).append(member)
    return by_group


def list_members_for_group(conn: sqlite3.Connection, group_id: str) -> list[dict]:
    """Return all transactions in a group with their basic fields."""
    return _members_for_groups(conn, [group_id]).get(group_id, [])


_NO_BALANCES = {"count": 0, "total": 0, "name": None}

_EMPTY_SUMMARY: dict = {
    "member_count": 0,
    "first_date": None,
    "last_date": None,
    "spend": 0,
    "received": 0,
    "your_share": 0,
    "has_people": False,
    "settle_up": [],
    "owed_to_you": _NO_BALANCES,
    "you_owe": _NO_BALANCES,
}


def empty_summary() -> dict:
    """Rollup for a group with no members yet (a fresh copy, never shared)."""
    return {
        **_EMPTY_SUMMARY,
        "settle_up": [],
        "owed_to_you": dict(_NO_BALANCES),
        "you_owe": dict(_NO_BALANCES),
    }


def _side_total(balances: list[dict]) -> dict:
    """Count, summed amount and (for exactly one person) the name of one side."""
    return {
        "count": len(balances),
        "total": round(sum(abs(b["net"]) for b in balances), 2),
        "name": balances[0]["name"] if len(balances) == 1 else None,
    }


def summarize_groups(conn: sqlite3.Connection, group_ids: list[str]) -> dict[str, dict]:
    """Per-group rollup for the header: date range, gross legs, your share, who owes.

    Two queries however many groups: every member, then the names of everyone tagged.
    spend/received are the gross legs (money out of / into your account).
    `settle_up` is the per-person list the card body shows, so header and body
    cannot disagree. `owed_to_you` and `you_owe` total its two sides separately
    (count, amount, and the name when it is one person): balances with opposite
    signs belong to different people and never cancel. `your_share` is what the
    group cost you after money that came back and money still owed to you:

        your_share = spend - received - sum(settle_up nets)

    A 880 stay split with Neha (she owes 440) is a share of 440; money you merely
    sent a person (transfer / refund) is owed back in full, so it is not your share.
    `has_people` says whether anyone is tagged, which separates "settled" (people
    tagged, nothing owed) from "nobody tagged, nothing to settle".
    """
    members_by_group = _members_for_groups(conn, group_ids)
    all_people = {p for ms in members_by_group.values() for m in ms for p in m["people"]}
    names = _person_names(conn, sorted(all_people))
    summary: dict[str, dict] = {}
    for group_id, members in members_by_group.items():
        dates = [m["txn_date"] for m in members]
        spend = sum(m["amount"] for m in members if m["debit_credit"] == "debit")
        received = sum(m["amount"] for m in members if m["debit_credit"] == "credit")
        settle_up = _settle_up(members, names)
        owed = sum(s["net"] for s in settle_up)
        summary[group_id] = {
            "member_count": len(members),
            "first_date": min(dates),
            "last_date": max(dates),
            "spend": spend,
            "received": received,
            "your_share": round(spend - received - owed, 2),
            "has_people": any(m["people"] for m in members),
            "settle_up": settle_up,
            "owed_to_you": _side_total([s for s in settle_up if s["net"] > 0]),
            "you_owe": _side_total([s for s in settle_up if s["net"] < 0]),
        }
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

    A balance that rounds to zero rupees (a float remainder) is not a balance and
    is left out. `people` holds person ids; `name` resolves them, falling back to
    the raw value for tags that predate the people table.
    """
    members = list_members_for_group(conn, group_id)
    names = _person_names(conn, sorted({p for m in members for p in m["people"]}))
    return _settle_up(members, names)


def _settle_up(members: list[dict], names: dict[str, str]) -> list[dict]:
    balances: dict[str, float] = {}
    for m in members:
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
    return [
        {"person": person, "name": names.get(person, person), "net": net}
        for person, net in sorted(balances.items(), key=lambda kv: -abs(kv[1]))
        if abs(net) >= 0.5
    ]


def _person_names(conn: sqlite3.Connection, person_ids: list[str]) -> dict[str, str]:
    if not person_ids:
        return {}
    placeholders = ",".join("?" * len(person_ids))
    rows = conn.execute(
        f"SELECT id, name FROM people WHERE id IN ({placeholders})", person_ids
    ).fetchall()
    return {r[0]: r[1] for r in rows}
