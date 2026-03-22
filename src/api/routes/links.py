"""Transaction link routes: create, delete, list."""
from __future__ import annotations

import sqlite3
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import get_db
from src.db.queries.links import delete_link, insert_link, list_links_for_transaction

router = APIRouter()

LINK_TYPES = Literal["split", "reimbursement", "refund", "transfer"]


class LinkCreate(BaseModel):
    txn_a: str
    txn_b: str
    link_type: LINK_TYPES
    note: Optional[str] = None


@router.post("", status_code=201)
def create_link(body: LinkCreate, conn: sqlite3.Connection = Depends(get_db)):
    if body.txn_a == body.txn_b:
        raise HTTPException(status_code=422, detail="Cannot link a transaction to itself")
    link = insert_link(conn, body.txn_a, body.txn_b, body.link_type, body.note)
    conn.commit()
    return link


@router.delete("/{link_id}", status_code=204)
def remove_link(link_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not delete_link(conn, link_id):
        raise HTTPException(status_code=404, detail="Link not found")
    conn.commit()


@router.get("/transaction/{transaction_id}")
def get_links(transaction_id: str, conn: sqlite3.Connection = Depends(get_db)):
    return list_links_for_transaction(conn, transaction_id)
