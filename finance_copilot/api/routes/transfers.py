"""Transfer routes: pair two payments by hand (Teach Me), and take a pair apart.

Pairing by itself is the pipeline's job (pipeline/pairing.py, after a statement
lands). These routes are the user's decisions: choosing a candidate Teach Me
offered, and Unpair. Both return an undo token for POST /annotations/review-queue/undo,
which restores the link, the two annotations and the recorded unpairings exactly.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from finance_copilot.api.deps import get_db
from finance_copilot.db.connection import write_transaction
from finance_copilot.db.queries.review_undo import UndoCapture
from finance_copilot.db.queries.transfers import link_pair, transfer_link, unpair, with_transfer
from finance_copilot.pipeline.pairing import check_can_pair

router = APIRouter()


class PairBody(BaseModel):
    transaction_id: str
    with_transaction_id: str


class UnpairBody(BaseModel):
    transaction_id: str


def _exists(conn: sqlite3.Connection, transaction_id: str) -> bool:
    return conn.execute("SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)).fetchone() is not None


@router.post("/pair")
def pair(body: PairBody, conn: sqlite3.Connection = Depends(get_db)):
    """Pair two payments as a transfer between the user's own accounts: one a debit,
    one a credit, the same amount, within three days, in different accounts, and
    neither already paired nor labelled by the user as something other than Self
    Transfers. 409 when they no longer fit (a payment was sorted or paired since
    the card was shown)."""
    ids = [body.transaction_id, body.with_transaction_id]
    if not all(_exists(conn, t) for t in ids):
        raise HTTPException(status_code=404, detail="That payment is gone. Reload the list.")
    with write_transaction(conn):
        # The check and the snapshot happen under the write lock, so a payment sorted or
        # paired by another request cannot slip between them and the write.
        fit = check_can_pair(conn, *ids)
        if fit is None:
            raise HTTPException(
                status_code=409,
                detail="These two payments can't be paired any more. One may have been paired or sorted since.",
            )
        capture = UndoCapture(conn, ids)
        link_pair(conn, *fit)
        token = capture.finish(conn)
    transfer = with_transfer(conn, [{"id": body.transaction_id}])[0]["transfer"]
    return {"transaction_ids": ids, "transfer": transfer, "undo_token": token}


@router.post("/unpair")
def unpair_payment(body: UnpairBody, conn: sqlite3.Connection = Depends(get_db)):
    """Take this payment's pair apart. Both payments go back to To sort (one the user
    labelled themselves keeps its label), and the pair is remembered so it is not made
    again. `unpaired` is the two payments, `to_sort` the ones that went back to To sort,
    and `account_labels` says which account each of the two is in (for the toast)."""
    if not _exists(conn, body.transaction_id):
        raise HTTPException(status_code=404, detail="That payment is gone. Reload the list.")
    with write_transaction(conn):
        link = transfer_link(conn, body.transaction_id)
        if link is None:
            raise HTTPException(status_code=409, detail="That payment is not paired.")
        ids = [link["txn_a"], link["txn_b"]]
        # Each side's link names the other's account, so the other's row says this one's.
        sides = with_transfer(conn, [{"id": t} for t in ids])
        account_labels = {sides[1]["id"]: sides[0]["transfer"]["account_label"], sides[0]["id"]: sides[1]["transfer"]["account_label"]}
        capture = UndoCapture(conn, ids)
        unpair(conn, body.transaction_id, remember=True)
        marks = ",".join("?" * len(ids))
        labelled = {r[0] for r in conn.execute(f"SELECT transaction_id FROM annotations WHERE transaction_id IN ({marks})", ids)}
        token = capture.finish(conn)
    return {
        "unpaired": ids,
        "to_sort": [t for t in ids if t not in labelled],
        "account_labels": account_labels,
        "undo_token": token,
    }
