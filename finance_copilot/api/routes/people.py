"""People/contacts routes: create, list, delete."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.queries.people import (
    create_person,
    delete_person,
    find_duplicate_person,
    get_person,
    list_people,
    people_transaction_stats,
    search_people,
    update_person,
)

router = APIRouter()


def _validate_match_token(upi: str | None) -> None:
    """A short match-token is a collision hazard: name tokens are word-prefix
    matched against counterparty names, so 'ma' would fire on MAHESH, MANOJ,
    MAX Fashion, … Require 3+ characters (VPA tokens with '@' are exact-match
    and exempt)."""
    if upi and "@" not in upi and len(upi.strip()) < 3:
        raise HTTPException(
            status_code=422,
            detail="Match token must be at least 3 characters (it matches by name prefix).",
        )


def _clean_person(conn: sqlite3.Connection, name: str, upi: str | None, exclude_id: str | None = None):
    """Trim the name and token, and refuse a second copy of the same person."""
    name = name.strip()
    upi = (upi or "").strip() or None
    if not name:
        raise HTTPException(status_code=422, detail="Give the person a name.")
    _validate_match_token(upi)
    clash = find_duplicate_person(conn, name, upi, exclude_id)
    if clash:
        have = f"You already have {clash['name']}"
        raise HTTPException(
            status_code=409,
            detail=f'{have} with the match "{clash["upi"]}".' if clash["upi"] else f"{have}.",
        )
    return name, upi


class PersonCreate(BaseModel):
    name: str
    upi: str | None = None
    relationship: str | None = None


@router.post("", status_code=201)
def create(body: PersonCreate, conn: sqlite3.Connection = Depends(get_db)):
    name, upi = _clean_person(conn, body.name, body.upi)
    person = create_person(conn, name, upi, body.relationship or None)
    conn.commit()
    return person


@router.get("")
def list_all(q: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """List people (optionally name/upi-filtered), each with match count and net
    sent/received computed from the transactions their match-token matches."""
    people = search_people(conn, q) if q else list_people(conn)
    stats = people_transaction_stats(conn)
    empty = {"matched": 0, "sent": 0, "received": 0, "net": 0}
    for p in people:
        p["stats"] = stats.get(p["id"], empty)
    return people


class PersonUpdate(BaseModel):
    name: str
    upi: str | None = None
    relationship: str | None = None


@router.patch("/{person_id}")
def update(person_id: str, body: PersonUpdate, conn: sqlite3.Connection = Depends(get_db)):
    if not get_person(conn, person_id):
        raise HTTPException(status_code=404, detail="Person not found")
    name, upi = _clean_person(conn, body.name, body.upi, exclude_id=person_id)
    person = update_person(conn, person_id, name, upi, body.relationship or None)
    conn.commit()
    return person


@router.delete("/{person_id}", status_code=204)
def remove(person_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not delete_person(conn, person_id):
        raise HTTPException(status_code=404, detail="Person not found")
    conn.commit()
