"""Transaction group routes: create groups, manage members, search."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.groups import (
    add_member,
    create_group,
    delete_group,
    empty_summary,
    find_group_by_name,
    get_group,
    list_groups_for_transaction,
    list_members_for_group,
    remove_member,
    search_groups,
    summarize_groups,
    update_member,
)
from finance_copilot.db.queries.payee import with_payee

router = APIRouter()

GROUP_TYPES = Literal["split", "reimbursement", "refund", "transfer", "event"]
ROLES = Literal["paid", "received", "partial"]


class GroupCreate(BaseModel):
    name: str
    note: str | None = None
    labels: list[str] = []


class MemberAdd(BaseModel):
    transaction_id: str
    role: ROLES | None = None
    people: list[str] = []
    txn_type: GROUP_TYPES | None = None


class MemberPatch(BaseModel):
    people: list[str] | None = None
    labels: list[str] | None = None
    txn_type: GROUP_TYPES | None = None
    role: ROLES | None = None


def _clean_group_name(conn: sqlite3.Connection, name: str, exclude_id: str | None = None) -> str:
    """Trim the name, and refuse a blank one or one another group already has."""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the group a name.")
    clash = find_group_by_name(conn, name, exclude_id)
    if clash:
        raise HTTPException(
            status_code=409, detail=f'You already have a group called "{clash["name"]}".'
        )
    return name


@router.post("", status_code=201)
def create(body: GroupCreate, conn: sqlite3.Connection = Depends(get_db)):
    name = _clean_group_name(conn, body.name)
    group = create_group(conn, name, (body.note or "").strip() or None, body.labels or None)
    conn.commit()
    return group


@router.get("")
def list_all(q: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """List groups (optionally name-filtered), each with the header rollup:
    date range, gross legs, your share, who owes, member count."""
    from finance_copilot.db.queries.groups import list_groups
    groups = search_groups(conn, q) if q else list_groups(conn)
    summaries = summarize_groups(conn, [g["id"] for g in groups])
    for g in groups:
        g["summary"] = summaries.get(g["id"], empty_summary())
    return groups


@router.get("/{group_id}")
def get(group_id: str, conn: sqlite3.Connection = Depends(get_db)):
    group = get_group(conn, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    group["members"] = with_payee(conn, list_members_for_group(conn, group_id))
    # Same rollup the list serves, so the open card's header refreshes from the
    # server after every membership change instead of keeping the list's copy.
    group["summary"] = summarize_groups(conn, [group_id]).get(group_id, empty_summary())
    return group


class GroupPatch(BaseModel):
    """Every field is optional; one that is left out stays as it is."""
    name: str | None = None
    note: str | None = None
    labels: list[str] | None = None


@router.patch("/{group_id}")
def patch(group_id: str, body: GroupPatch, conn: sqlite3.Connection = Depends(get_db)):
    if not get_group(conn, group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    if body.name is not None:
        conn.execute(
            "UPDATE transaction_groups SET name = ? WHERE id = ?",
            (_clean_group_name(conn, body.name, exclude_id=group_id), group_id),
        )
    if "note" in body.model_fields_set:
        conn.execute(
            "UPDATE transaction_groups SET note = ? WHERE id = ?",
            ((body.note or "").strip() or None, group_id),
        )
    if body.labels is not None:
        from finance_copilot.db.queries.common import dump_string_list

        conn.execute(
            "UPDATE transaction_groups SET labels = ? WHERE id = ?",
            (dump_string_list(body.labels) if body.labels else None, group_id),
        )
    conn.commit()
    return get_group(conn, group_id)


@router.delete("/{group_id}", status_code=204)
def remove(group_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not delete_group(conn, group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    conn.commit()


@router.post("/{group_id}/members", status_code=201)
def add(group_id: str, body: MemberAdd, conn: sqlite3.Connection = Depends(get_db)):
    if not get_group(conn, group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    add_member(conn, group_id, body.transaction_id, body.role, body.people or None, txn_type=body.txn_type)
    conn.commit()
    return {"group_id": group_id, "transaction_id": body.transaction_id, "role": body.role, "people": body.people, "txn_type": body.txn_type}


@router.patch("/{group_id}/members/{transaction_id}")
def patch_member(
    group_id: str, transaction_id: str, body: MemberPatch, conn: sqlite3.Connection = Depends(get_db)
):
    if not get_group(conn, group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    update_member(conn, group_id, transaction_id, body.people, body.labels, body.txn_type, body.role)
    conn.commit()
    return {"group_id": group_id, "transaction_id": transaction_id, "people": body.people, "labels": body.labels, "txn_type": body.txn_type, "role": body.role}


@router.delete("/{group_id}/members/{transaction_id}", status_code=204)
def remove_member_route(
    group_id: str, transaction_id: str, conn: sqlite3.Connection = Depends(get_db)
):
    if not remove_member(conn, group_id, transaction_id):
        raise HTTPException(status_code=404, detail="Member not found")
    conn.commit()


@router.get("/for-transaction/{transaction_id}")
def groups_for_transaction(transaction_id: str, conn: sqlite3.Connection = Depends(get_db)):
    return list_groups_for_transaction(conn, transaction_id)
