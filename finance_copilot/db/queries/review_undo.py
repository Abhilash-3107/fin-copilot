"""Undo for Teach Me answers.

An answer (confirm, correct, hand label, group label, skip) changes three kinds
of stored state: annotation rows, a payment's snooze, and the review feedback
counters. Pairing two payments as a transfer (and unpairing them) also changes the
transfer link between them and the pairs the user took apart, so those are part of
a payment's snapshot too. Learned rules are computed from annotations, so restoring the rows
restores them too. `UndoCapture` snapshots that state before an answer and, once
the answer (and the re-decisions it caused) is done, stores what changed under a
token. `undo` restores it exactly, or refuses when a payment has changed since.

Counters are recorded from the answer's own feedback calls (`capture.feedback`),
never by diffing the table, so an answer another request lands meanwhile is not
swept into this record. Embeddings (vec_items, embedding_meta) are not restored:
they describe the payment's text, not its label, so a stale one is harmless.

This is the small precursor of R7's change sets: it is isolated here so that
work can replace it. Only the last day and the last RETAIN_ANSWERS answers are kept.
"""
from __future__ import annotations

import json
import sqlite3

import ulid

from finance_copilot.db.queries.feedback_stats import record_feedback

RETAIN_ANSWERS = 50
RETAIN_DAYS = 1
_CHUNK = 500
_FEEDBACK_COLUMNS = ["confirmed", "refined", "corrected"]


class UndoError(Exception):
    """The answer cannot be undone. The message is written for the person."""


def _chunks(ids: list[str]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i : i + _CHUNK]


def _snapshot(conn: sqlite3.Connection, transaction_ids: list[str]) -> dict[str, dict]:
    """{transaction_id: {"annotation": whole row or None, "snoozed_at": str or None,
    "link": the transfer link row or None, "unpaired": [partner ids]}} for every
    payment that exists."""
    out: dict[str, dict] = {}
    for chunk in _chunks(transaction_ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT id, review_snoozed_at FROM transactions WHERE id IN ({marks})", chunk):
            out[row["id"]] = {"annotation": None, "snoozed_at": row["review_snoozed_at"], "link": None, "unpaired": []}
        for row in conn.execute(f"SELECT * FROM annotations WHERE transaction_id IN ({marks})", chunk):
            out[row["transaction_id"]]["annotation"] = dict(row)
        for row in conn.execute(
            f"SELECT * FROM transaction_links WHERE link_type = 'transfer' AND (txn_a IN ({marks}) OR txn_b IN ({marks}))",
            (*chunk, *chunk),
        ):
            for side in (row["txn_a"], row["txn_b"]):
                if side in out:
                    out[side]["link"] = dict(row)
        for row in conn.execute(
            f"SELECT txn_a, txn_b FROM transfer_unpaired WHERE txn_a IN ({marks}) OR txn_b IN ({marks}) "
            "ORDER BY txn_a, txn_b",
            (*chunk, *chunk),
        ):
            for side, partner in ((row["txn_a"], row["txn_b"]), (row["txn_b"], row["txn_a"])):
                if side in out:
                    out[side]["unpaired"].append(partner)
    return out


def _with_partners(conn: sqlite3.Connection, transaction_ids: list[str]) -> list[str]:
    """These payments and the payments they are paired with in a transfer. An answer
    that gives a paired payment another category takes the pair apart, which changes
    the partner too (its pair label goes), so the partner is part of what an undo
    must put back."""
    out = list(dict.fromkeys(transaction_ids))
    for chunk in _chunks(list(out)):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT txn_a, txn_b FROM transaction_links WHERE link_type = 'transfer' "
            f"AND (txn_a IN ({marks}) OR txn_b IN ({marks}))",
            (*chunk, *chunk),
        ):
            out.extend(t for t in (row["txn_a"], row["txn_b"]) if t not in out)
    return out


def _complete(state: dict | None) -> dict | None:
    """A snapshot stored before pairing existed has no link or unpaired entry."""
    if state is None:
        return None
    return {"link": None, "unpaired": [], **state}


class UndoCapture:
    """Snapshot before an answer; `finish` stores the record and returns its token.

    `transaction_ids` are the payments the answer is about, and the payments
    they are paired with in a transfer (an answer can take a pair apart). The
    ones it may cause to be re-decided are added with `include`, once they are
    known and before anything re-decides them (they are only recorded if they
    changed).
    The answer's calibration counters go through `feedback`.
    """

    def __init__(self, conn: sqlite3.Connection, transaction_ids: list[str]):
        self._ids = _with_partners(conn, transaction_ids)
        self._before = _snapshot(conn, self._ids)
        # (source, category) -> what this answer added, and the row as it was
        self._counters: dict[tuple[str, str], dict] = {}

    def include(self, conn: sqlite3.Connection, transaction_ids: list[str]) -> None:
        """Also watch these payments: snapshot them now, as they are before any change."""
        new = [t for t in _with_partners(conn, transaction_ids) if t not in self._before]
        self._ids.extend(new)
        self._before.update(_snapshot(conn, new))

    def feedback(self, conn: sqlite3.Connection, source: str, category: str, feedback_type: str) -> None:
        """record_feedback, noting that this answer is what added the count."""
        key = (source, category)
        if key not in self._counters:
            row = conn.execute(
                "SELECT updated_at FROM feedback_stats WHERE source = ? AND category = ?", key
            ).fetchone()
            self._counters[key] = {
                "delta": [0, 0, 0], "existed": row is not None,
                "updated_at": row["updated_at"] if row else None,
            }
        self._counters[key]["delta"][_FEEDBACK_COLUMNS.index(feedback_type)] += 1
        record_feedback(conn, source, category, feedback_type)

    def finish(self, conn: sqlite3.Connection) -> str:
        """Record what changed since the snapshot. Commits."""
        after = _snapshot(conn, self._ids)
        rows = [
            {"transaction_id": tid, "before": self._before[tid], "after": after.get(tid)}
            for tid in self._before
            if self._before[tid] != after.get(tid)
        ]
        feedback = []
        for (source, category), counter in self._counters.items():
            row = conn.execute(
                "SELECT updated_at FROM feedback_stats WHERE source = ? AND category = ?", (source, category)
            ).fetchone()
            feedback.append({
                "source": source, "category": category, **counter,
                "updated_after": row["updated_at"] if row else None,
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


def _restore_transfers(conn: sqlite3.Connection, states: list[tuple[str, dict]]) -> None:
    """Put the transfer links and the user's unpairings of these payments back as
    they were: first clear what is there, then write what each had before."""
    for tid, _ in states:
        conn.execute(
            "DELETE FROM transaction_links WHERE link_type = 'transfer' AND (txn_a = ? OR txn_b = ?)", (tid, tid)
        )
        conn.execute("DELETE FROM transfer_unpaired WHERE txn_a = ? OR txn_b = ?", (tid, tid))
    for tid, before in states:
        link = before["link"]
        if link:
            conn.execute(
                "INSERT OR IGNORE INTO transaction_links (id, txn_a, txn_b, link_type, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (link["id"], link["txn_a"], link["txn_b"], link["link_type"], link["note"], link["created_at"]),
            )
        for partner in before["unpaired"]:
            low, high = sorted((tid, partner))
            conn.execute("INSERT OR IGNORE INTO transfer_unpaired (txn_a, txn_b) VALUES (?, ?)", (low, high))


def _refuse_a_transfer_inside_one_account(conn: sqlite3.Connection, states: list[dict]) -> None:
    """Money does not transfer within an account. A statement moved since the answer
    can put the two payments of a link this undo would restore into one account (the
    move took the pair apart for that reason), so the undo is refused rather than
    bringing the pair back."""
    for state in states:
        link = state["link"]
        if not link:
            continue
        accounts = {
            row["account_id"]
            for row in conn.execute(
                "SELECT s.account_id FROM transactions t JOIN statements s ON s.id = t.statement_id "
                "WHERE t.id IN (?, ?)", (link["txn_a"], link["txn_b"]),
            )
        }
        if len(accounts) == 1:
            raise UndoError("Those payments have changed since, so this answer can't be undone.")


def undo(conn: sqlite3.Connection, token: str) -> list[str]:
    """Restore the state an answer changed; returns the payments put back.
    Raises UndoError when the token is unknown, expired, already used, or a
    payment it covers has changed since (nothing is then restored). The check
    and the restore are one transaction under the write lock, so nothing can
    change a payment between them. Commits."""
    conn.commit()  # no read transaction left open: BEGIN IMMEDIATE must be the first
    conn.execute("BEGIN IMMEDIATE")
    try:
        record = conn.execute("SELECT undone_at, changes FROM review_undo WHERE token = ?", (token,)).fetchone()
        if record is None:
            raise UndoError("That answer can no longer be undone.")
        if record["undone_at"] is not None:
            raise UndoError("That answer was already undone.")
        changes = json.loads(record["changes"])

        now = _snapshot(conn, [row["transaction_id"] for row in changes["rows"]])
        if any(now.get(row["transaction_id"]) != _complete(row["after"]) for row in changes["rows"]):
            raise UndoError("Those payments have changed since, so this answer can't be undone.")

        _refuse_a_transfer_inside_one_account(conn, [_complete(row["before"]) for row in changes["rows"]])
        _restore_transfers(conn, [(row["transaction_id"], _complete(row["before"])) for row in changes["rows"]])
        for row in changes["rows"]:
            tid, before = row["transaction_id"], row["before"]
            _write_annotation(conn, tid, before["annotation"])
            conn.execute("UPDATE transactions SET review_snoozed_at = ? WHERE id = ?", (before["snoozed_at"], tid))
        for entry in changes["feedback"]:
            _subtract_feedback(conn, entry)
        conn.execute("UPDATE review_undo SET undone_at = datetime('now') WHERE token = ?", (token,))
        conn.commit()
    except BaseException:
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
    # Put the timestamp back too, unless another answer touched the row since.
    if entry.get("updated_at"):
        conn.execute(
            "UPDATE feedback_stats SET updated_at = ? WHERE source = ? AND category = ? AND updated_at = ?",
            (entry["updated_at"], entry["source"], entry["category"], entry["updated_after"]),
        )
    if not entry["existed"]:
        conn.execute(
            "DELETE FROM feedback_stats WHERE source = ? AND category = ? "
            "AND confirmed = 0 AND refined = 0 AND corrected = 0",
            (entry["source"], entry["category"]),
        )
