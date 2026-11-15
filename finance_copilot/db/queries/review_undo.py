"""Undo for Teach Me answers.

An answer (confirm, correct, hand label, group label, skip) changes three kinds
of stored state: annotation rows, a payment's snooze, and the review feedback
counters. Learned rules are computed from annotations, so restoring the rows
restores them too. `UndoCapture` snapshots that state before an answer and, once
the answer (and the re-decisions it caused) is done, stores what changed under a
token. `undo` restores it exactly, or refuses when a payment has changed since.

This is the small precursor of R7's change sets: it is isolated here so that
work can replace it. Only the last day and the last RETAIN_ANSWERS answers are kept.
"""
from __future__ import annotations

import json
import sqlite3

import ulid

RETAIN_ANSWERS = 50
RETAIN_DAYS = 1
_CHUNK = 500


class UndoError(Exception):
    """The answer cannot be undone. The message is written for the person."""


def _chunks(ids: list[str]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i : i + _CHUNK]


def _snapshot(conn: sqlite3.Connection, transaction_ids: list[str]) -> dict[str, dict]:
    """{transaction_id: {"annotation": whole row or None, "snoozed_at": str or None}}
    for every payment that exists."""
    out: dict[str, dict] = {}
    for chunk in _chunks(transaction_ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT id, review_snoozed_at FROM transactions WHERE id IN ({marks})", chunk):
            out[row["id"]] = {"annotation": None, "snoozed_at": row["review_snoozed_at"]}
        for row in conn.execute(f"SELECT * FROM annotations WHERE transaction_id IN ({marks})", chunk):
            out[row["transaction_id"]]["annotation"] = dict(row)
    return out


def _feedback(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[int, int, int]]:
    """The whole counter table (one row per source and category: it stays small)."""
    return {
        (r["source"], r["category"]): (r["confirmed"], r["refined"], r["corrected"])
        for r in conn.execute("SELECT source, category, confirmed, refined, corrected FROM feedback_stats")
    }


class UndoCapture:
    """Snapshot before an answer; `finish` stores the record and returns its token.

    `transaction_ids` are the payments the answer is about, `reevaluated` the
    ones it may cause to be re-decided (they are only recorded if they changed).
    """

    def __init__(self, conn: sqlite3.Connection, transaction_ids: list[str], reevaluated: list[str] = ()):
        self._ids = list(dict.fromkeys([*transaction_ids, *reevaluated]))
        self._before = _snapshot(conn, self._ids)
        self._feedback_before = _feedback(conn)

    def finish(self, conn: sqlite3.Connection) -> str:
        """Record what changed since the snapshot. Commits."""
        after = _snapshot(conn, self._ids)
        rows = [
            {"transaction_id": tid, "before": self._before[tid], "after": after.get(tid)}
            for tid in self._before
            if self._before[tid] != after.get(tid)
        ]
        feedback_after = _feedback(conn)
        feedback = []
        for key, now in feedback_after.items():
            was = self._feedback_before.get(key)
            if now != (was or (0, 0, 0)):
                feedback.append({
                    "source": key[0], "category": key[1],
                    "delta": [n - w for n, w in zip(now, was or (0, 0, 0), strict=True)],
                    "existed": was is not None,
                })
        token = str(ulid.ULID())
        conn.execute(
            "INSERT INTO review_undo (token, changes) VALUES (?, ?)",
            (token, json.dumps({"rows": rows, "feedback": feedback})),
        )
        conn.execute(f"DELETE FROM review_undo WHERE created_at < datetime('now', '-{RETAIN_DAYS} days')")
        conn.execute(
            "DELETE FROM review_undo WHERE rowid NOT IN (SELECT rowid FROM review_undo ORDER BY rowid DESC LIMIT ?)",
            (RETAIN_ANSWERS,),
        )
        conn.commit()
        return token


def _write_annotation(conn: sqlite3.Connection, transaction_id: str, row: dict | None) -> None:
    """Make a payment's annotation exactly `row` (None: no annotation). The
    column names come from the row snapshot, which came from the table itself."""
    if row is None:
        conn.execute("DELETE FROM annotations WHERE transaction_id = ?", (transaction_id,))
        return
    columns = list(row)
    names = ", ".join(f'"{c}"' for c in columns)
    assignments = ", ".join(f'"{c}" = excluded."{c}"' for c in columns if c != "transaction_id")
    conn.execute(
        f"INSERT INTO annotations ({names}) VALUES ({', '.join('?' * len(columns))}) "
        f"ON CONFLICT(transaction_id) DO UPDATE SET {assignments}",
        [row[c] for c in columns],
    )


def undo(conn: sqlite3.Connection, token: str) -> list[str]:
    """Restore the state an answer changed; returns the payments put back.
    Raises UndoError when the token is unknown, expired, already used, or a
    payment it covers has changed since (nothing is then restored). Commits."""
    record = conn.execute("SELECT undone_at, changes FROM review_undo WHERE token = ?", (token,)).fetchone()
    if record is None:
        raise UndoError("That answer can no longer be undone.")
    if record["undone_at"] is not None:
        raise UndoError("That answer was already undone.")
    changes = json.loads(record["changes"])

    now = _snapshot(conn, [row["transaction_id"] for row in changes["rows"]])
    if any(now.get(row["transaction_id"]) != row["after"] for row in changes["rows"]):
        raise UndoError("Those payments have changed since, so this answer can't be undone.")

    try:
        for row in changes["rows"]:
            tid, before = row["transaction_id"], row["before"]
            _write_annotation(conn, tid, before["annotation"])
            conn.execute("UPDATE transactions SET review_snoozed_at = ? WHERE id = ?", (before["snoozed_at"], tid))
        for entry in changes["feedback"]:
            _subtract_feedback(conn, entry)
        conn.execute("UPDATE review_undo SET undone_at = datetime('now') WHERE token = ?", (token,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return [row["transaction_id"] for row in changes["rows"]]


def _subtract_feedback(conn: sqlite3.Connection, entry: dict) -> None:
    """Take the answer's share back out of one counter row, leaving what other
    answers added; a row the answer created goes when nothing else is on it."""
    confirmed, refined, corrected = entry["delta"]
    conn.execute(
        "UPDATE feedback_stats SET confirmed = MAX(confirmed - ?, 0), refined = MAX(refined - ?, 0), "
        "corrected = MAX(corrected - ?, 0) WHERE source = ? AND category = ?",
        (confirmed, refined, corrected, entry["source"], entry["category"]),
    )
    if not entry["existed"]:
        conn.execute(
            "DELETE FROM feedback_stats WHERE source = ? AND category = ? "
            "AND confirmed = 0 AND refined = 0 AND corrected = 0",
            (entry["source"], entry["category"]),
        )
