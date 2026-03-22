"""Transaction group routes: create groups, manage members, search."""
from __future__ import annotations

import sqlite3
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from financebot.api.deps import get_db
from financebot.db.queries.groups import (
    add_member,
    create_group,
    delete_group,
    get_group,
    list_groups_for_transaction,
    list_members_for_group,
    remove_member,
    search_groups,
    update_member,
)

router = APIRouter()

GROUP_TYPES = Literal["split", "reimbursement", "refund", "transfer", "event"]
ROLES = Literal["paid", "received", "partial"]


class GroupCreate(BaseModel):
    name: str
    note: Optional[str] = None
    labels: list[str] = []


class MemberAdd(BaseModel):
    transaction_id: str
    role: Optional[ROLES] = None
    people: list[str] = []
    txn_type: Optional[GROUP_TYPES] = None


class MemberPatch(BaseModel):
    people: Optional[list[str]] = None
    labels: Optional[list[str]] = None
    txn_type: Optional[GROUP_TYPES] = None


@router.post("", status_code=201)
def create(body: GroupCreate, conn: sqlite3.Connection = Depends(get_db)):
    group = create_group(conn, body.name, body.note, body.labels or None)
    conn.commit()
    return group


@router.get("")
def list_all(q: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """List groups, optionally filtered by name search."""
    from financebot.db.queries.groups import list_groups
    return search_groups(conn, q) if q else list_groups(conn)


@router.get("/{group_id}")
def get(group_id: str, conn: sqlite3.Connection = Depends(get_db)):
    group = get_group(conn, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    group["members"] = list_members_for_group(conn, group_id)
    return group


class GroupPatch(BaseModel):
    labels: Optional[list[str]] = None


@router.patch("/{group_id}")
def patch(group_id: str, body: GroupPatch, conn: sqlite3.Connection = Depends(get_db)):
    if not get_group(conn, group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    if body.labels is not None:
        conn.execute(
            "UPDATE transaction_groups SET labels = ? WHERE id = ?",
            (",".join(body.labels) or None, group_id),
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
    update_member(conn, group_id, transaction_id, body.people, body.labels, body.txn_type)
    conn.commit()
    return {"group_id": group_id, "transaction_id": transaction_id, "people": body.people, "labels": body.labels, "txn_type": body.txn_type}


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
