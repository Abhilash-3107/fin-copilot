"""Account routes: list the accounts statements belong to, and name them."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.accounts import (
    NAME_MAX_LENGTH,
    get_account,
    list_accounts,
    set_account_name,
)

router = APIRouter()


@router.get("")
def get_accounts(conn: sqlite3.Connection = Depends(get_db)):
    """Every account that holds a statement, with its label, how many statements it
    has, the dates its payments span and its latest known balance."""
    return list_accounts(conn)


class AccountPatch(BaseModel):
    name: str


@router.patch("/{account_id}")
def patch_account(
    account_id: str,
    body: AccountPatch,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Name an account. A blank name clears it, back to the bank and last digits."""
    name = body.name.strip()
    if len(name) > NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"An account name can be at most {NAME_MAX_LENGTH} characters."
        )
    if get_account(conn, account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    set_account_name(conn, account_id, name)
    conn.commit()
    return get_account(conn, account_id)
