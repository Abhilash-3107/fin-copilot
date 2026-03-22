"""People/contacts routes: create, list, delete."""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from financebot.api.deps import get_db
from financebot.db.queries.people import (
    create_person,
    delete_person,
    list_people,
    search_people,
)

router = APIRouter()


class PersonCreate(BaseModel):
    name: str
    upi: Optional[str] = None


@router.post("", status_code=201)
def create(body: PersonCreate, conn: sqlite3.Connection = Depends(get_db)):
    person = create_person(conn, body.name, body.upi)
    conn.commit()
    return person


@router.get("")
def list_all(q: str = "", conn: sqlite3.Connection = Depends(get_db)):
    return search_people(conn, q) if q else list_people(conn)


@router.delete("/{person_id}", status_code=204)
def remove(person_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not delete_person(conn, person_id):
        raise HTTPException(status_code=404, detail="Person not found")
    conn.commit()
