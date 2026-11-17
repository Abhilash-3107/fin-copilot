"""Undo of a Teach Me answer (R2 part C): every answer returns a token, and the
token restores exactly the state the answer changed."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from finance_copilot.db.connection import get_connection, init_db


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
    c.commit()
    return c


@pytest.fixture
def client(conn):
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.main import app

    app.dependency_overrides[api_get_db] = lambda: conn
    with patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False), \
            patch("finance_copilot.api.routes.annotations.embed_transactions"):
        yield TestClient(app)
    app.dependency_overrides.pop(api_get_db, None)


def _pay(conn, txn_id, payee="CHAI WALA", label=None, *, source="llm", confidence=0.4, day=1, tags=None):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
        "VALUES (?, 's', ?, 100, 'debit', ?, ?)",
        (txn_id, f"2026-06-{day:02d}", f"UPI/{payee}/12345678{day:04d}/UPI", payee),
    )
    if label is not None:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source, tags, merchant, reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Old merchant', '{\"why\": 1}')",
            (f"a_{txn_id}", txn_id, label, confidence, source, tags),
        )
    conn.commit()


def _state(conn):
    """Everything an answer can touch, as plain data."""
    return {
        "annotations": [dict(r) for r in conn.execute("SELECT * FROM annotations ORDER BY transaction_id")],
        "snoozes": [tuple(r) for r in conn.execute("SELECT id, review_snoozed_at FROM transactions ORDER BY id")],
        "feedback": [
            tuple(r) for r in conn.execute(
                "SELECT source, category, confirmed, refined, corrected FROM feedback_stats ORDER BY source, category"
            )
        ],
    }


def _undo(client, token):
    return client.post("/api/annotations/review-queue/undo", json={"token": token})


def _label(client, ids, category="Shopping", **extra):
    return client.post("/api/annotations/bulk-label", json={"transaction_ids": ids, "label": {"category": category, **extra}})


class TestEveryAnswerCanBeUndone:
    @staticmethod
    def _round_trip(client, conn, answer):
        before = _state(conn)
        resp = answer()
        assert resp.status_code == 200, resp.text
        token = resp.json()["undo_token"]
        assert token
        assert _state(conn) != before, "the answer should have changed something"
        undone = _undo(client, token)
        assert undone.status_code == 200, undone.text
        assert _state(conn) == before
        return token

    def test_confirming_a_guess(self, client, conn):
        _pay(conn, "t", label="Shopping")
        self._round_trip(client, conn, lambda: client.post("/api/annotations/a_t/confirm", json={"category": "Shopping"}))
        assert conn.execute("SELECT source FROM annotations").fetchone()[0] == "llm"

    def test_correcting_a_guess_in_a_group(self, client, conn):
        _pay(conn, "a", label="Shopping", day=1)
        _pay(conn, "b", label="Shopping", day=2)
        _pay(conn, "c", label="Entertainment", day=3, tags='["x"]')
        self._round_trip(client, conn, lambda: _label(client, ["a", "b", "c"], "Health", merchant="Chai Wala", tags=["y"]))

    def test_a_hand_label_on_a_bare_row_is_deleted_again(self, client, conn):
        _pay(conn, "bare")
        _pay(conn, "guess", label="Shopping", day=2)
        self._round_trip(client, conn, lambda: _label(client, ["bare", "guess"], "Health"))
        assert conn.execute("SELECT COUNT(*) FROM annotations WHERE transaction_id = 'bare'").fetchone()[0] == 0

    def test_skipping_a_payment(self, client, conn):
        _pay(conn, "t", label="Shopping")
        self._round_trip(client, conn, lambda: client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"}))
        assert [i["transaction_id"] for i in client.get("/api/annotations/review-queue").json()] == ["t"]

    def test_answering_a_skipped_payment_brings_its_snooze_back_on_undo(self, client, conn):
        _pay(conn, "t", label="Shopping")
        client.post("/api/annotations/review-queue/skip", json={"transaction_id": "t"})
        before = _state(conn)
        token = _label(client, ["t"], "Health").json()["undo_token"]
        assert conn.execute("SELECT review_snoozed_at FROM transactions").fetchone()[0] is None
        assert _undo(client, token).status_code == 200
        assert _state(conn) == before

    def test_the_machine_labels_an_answer_re_decided_come_back_too(self, client, conn):
        """Two earlier hand labels plus this answer make three agreeing ones: the
        payee's other guess is re-decided from the new learned rule. Undo puts that
        row back as the model had it, not just the answered one."""
        _pay(conn, "m1", label="Shopping", source="manual", confidence=1.0, day=1)
        _pay(conn, "m2", label="Shopping", source="manual", confidence=1.0, day=2)
        _pay(conn, "answered", label="Entertainment", day=3)
        _pay(conn, "other", label="Health", day=4, confidence=0.3)
        before = _state(conn)
        # The payee is answered one payment at a time, so "other" is not in the answer.
        body = _label(client, ["answered"], "Shopping").json()
        assert any(r["transaction_id"] == "other" for r in body["reevaluated"]), body
        assert conn.execute("SELECT category FROM annotations WHERE transaction_id = 'other'").fetchone()[0] == "Shopping"
        assert _undo(client, body["undo_token"]).status_code == 200
        assert _state(conn) == before


class TestUndoRefusals:
    def test_a_token_works_once(self, client, conn):
        _pay(conn, "t", label="Shopping")
        token = _label(client, ["t"], "Health").json()["undo_token"]
        assert _undo(client, token).status_code == 200
        again = _undo(client, token)
        assert again.status_code == 409
        assert again.json()["detail"] == "That answer was already undone."

    def test_an_unknown_token_is_refused(self, client):
        resp = _undo(client, "nope")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "That answer can no longer be undone."

    def test_an_old_answer_expires(self, client, conn):
        _pay(conn, "t", label="Shopping")
        token = _label(client, ["t"], "Health").json()["undo_token"]
        conn.execute("UPDATE review_undo SET created_at = datetime('now', '-2 days')")
        conn.commit()
        _pay(conn, "u", label="Shopping", payee="OTHER")
        _label(client, ["u"], "Health")  # a later answer prunes the expired record
        resp = _undo(client, token)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "That answer can no longer be undone."

    def test_only_the_most_recent_answers_are_kept(self, client, conn):
        for i in range(53):
            _pay(conn, f"t{i}", payee=f"PAYEE {i}", label="Shopping")
        tokens = [_label(client, [f"t{i}"], "Health").json()["undo_token"] for i in range(53)]
        assert conn.execute("SELECT COUNT(*) FROM review_undo").fetchone()[0] == 50
        assert _undo(client, tokens[0]).status_code == 409
        assert _undo(client, tokens[-1]).status_code == 200

    def test_a_payment_changed_since_blocks_the_undo_and_changes_nothing(self, client, conn):
        _pay(conn, "a", label="Shopping", day=1)
        _pay(conn, "b", label="Shopping", payee="OTHER", day=2)
        token = _label(client, ["a", "b"], "Health").json()["undo_token"]
        conn.execute("UPDATE annotations SET category = 'Travel' WHERE transaction_id = 'b'")
        conn.commit()
        after = _state(conn)
        resp = _undo(client, token)
        assert resp.status_code == 409
        assert "changed since" in resp.json()["detail"]
        assert _state(conn) == after

    def test_a_payment_that_is_gone_blocks_the_undo(self, client, conn):
        _pay(conn, "t", label="Shopping")
        token = _label(client, ["t"], "Health").json()["undo_token"]
        conn.execute("DELETE FROM annotations")
        conn.execute("DELETE FROM transactions")
        conn.commit()
        assert _undo(client, token).status_code == 409


class TestFeedbackCounters:
    def test_a_counter_other_answers_added_to_survives_the_undo(self, client, conn):
        _pay(conn, "a", label="Shopping", day=1)
        _pay(conn, "b", payee="OTHER", label="Shopping", day=2)
        first = _label(client, ["a"], "Health").json()["undo_token"]
        _label(client, ["b"], "Health")
        assert conn.execute("SELECT corrected FROM feedback_stats WHERE category = 'Shopping'").fetchone()[0] == 2
        # The first answer's counter is undone even though a later answer added to the same row.
        assert _undo(client, first).status_code == 200
        assert conn.execute("SELECT corrected FROM feedback_stats WHERE category = 'Shopping'").fetchone()[0] == 1


class TestDeleteEverything:
    def test_it_forgets_the_undo_records(self, client, conn):
        _pay(conn, "t", label="Shopping")
        token = _label(client, ["t"], "Health").json()["undo_token"]
        assert client.post("/api/data/delete-all").status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM review_undo").fetchone()[0] == 0
        assert _undo(client, token).status_code == 409


def _vec(seed):
    from finance_copilot.config import current_embedding_dim

    v = [0.0] * current_embedding_dim()
    v[0], v[1] = 1.0, seed
    return v


class TestNeighboursOfAnUnembeddedAnswer:
    """A payment answered before it was ever embedded has no vector until the
    answer is stored; its neighbours are only found once it has one, so the
    search must run after the embed, and the neighbours must still be undone."""

    @pytest.fixture
    def memory(self, conn):
        from finance_copilot.config import current_embedding_model
        from finance_copilot.db.queries.embeddings import ensure_vec_dimension, upsert_embedding

        ensure_vec_dimension(conn)
        _pay(conn, "taught", payee="BEAN THERE CAFE", day=1)
        _pay(conn, "sib", payee="BEAN THERE CAFE BLR", label="Miscellaneous", confidence=0.17, day=5)
        upsert_embedding(conn, "sib", _vec(0.0), current_embedding_model(), "h")
        conn.commit()

        def embed(c, ids):
            for tid in ids if isinstance(ids, list) else [ids]:
                upsert_embedding(c, tid, _vec(0.0), current_embedding_model(), "h")
            c.commit()
            return True

        with patch("finance_copilot.api.routes.annotations.embed_transactions", side_effect=embed), \
                patch("finance_copilot.api.routes.annotations.embed_transaction", side_effect=embed):
            yield conn

    def test_a_hand_label_re_decides_the_neighbour_and_undo_puts_it_back(self, client, memory):
        before = _state(memory)
        body = _label(client, ["taught"], "Food & Dining").json()
        assert [r["transaction_id"] for r in body["reevaluated"]] == ["sib"]
        assert memory.execute("SELECT category FROM annotations WHERE transaction_id = 'sib'").fetchone()[0] == "Food & Dining"
        assert _undo(client, body["undo_token"]).status_code == 200
        assert _state(memory) == before

    def test_confirming_re_decides_the_neighbour_too(self, client, memory):
        memory.execute(
            "INSERT INTO annotations (id, transaction_id, category, confidence, source) "
            "VALUES ('a_taught', 'taught', 'Food & Dining', 0.3, 'llm')"
        )
        memory.commit()
        before = _state(memory)
        body = client.post("/api/annotations/a_taught/confirm", json={"category": "Food & Dining"}).json()
        assert [r["transaction_id"] for r in body["reevaluated"]] == ["sib"]
        assert _undo(client, body["undo_token"]).status_code == 200
        assert _state(memory) == before


class TestCountersBelongToTheirAnswer:
    def test_another_answer_landing_meanwhile_is_not_part_of_this_undo(self, client, conn):
        """The counters an undo takes back are the ones this answer's own calls
        added, not whatever changed in the table while it ran."""
        from finance_copilot.db.queries.feedback_stats import record_feedback

        _pay(conn, "a", label="Shopping", day=1)

        def another_answer(c, ids):
            record_feedback(c, "llm", "Travel", "corrected")  # a concurrent answer's counter
            c.commit()

        with patch("finance_copilot.api.routes.annotations.embed_transactions", side_effect=another_answer):
            token = _label(client, ["a"], "Health").json()["undo_token"]
        record = json.loads(conn.execute("SELECT changes FROM review_undo WHERE token = ?", (token,)).fetchone()[0])
        assert [(f["source"], f["category"]) for f in record["feedback"]] == [("llm", "Shopping")]
        assert _undo(client, token).status_code == 200
        assert conn.execute("SELECT corrected FROM feedback_stats WHERE category = 'Travel'").fetchone()[0] == 1

    def test_undo_restores_the_counter_timestamp(self, client, conn):
        conn.execute(
            "INSERT INTO feedback_stats (source, category, confirmed, refined, corrected, updated_at) "
            "VALUES ('llm', 'Shopping', 1, 0, 0, '2026-01-01 00:00:00')"
        )
        _pay(conn, "a", label="Shopping")
        token = _label(client, ["a"], "Health").json()["undo_token"]
        assert _undo(client, token).status_code == 200
        assert conn.execute("SELECT updated_at FROM feedback_stats WHERE category = 'Shopping'").fetchone()[0] == "2026-01-01 00:00:00"


class TestUndoIsOneTransaction:
    def test_the_check_and_the_writes_hold_one_write_lock(self, tmp_path):
        """Another connection cannot slip a change in between the "changed since"
        check and the restore."""
        import sqlite3

        path = str(tmp_path / "undo.db")
        c = get_connection(path)
        init_db(c)
        c.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 't', '1', '2026-06')")
        c.commit()
        _pay(c, "t", label="Shopping")
        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        app.dependency_overrides[api_get_db] = lambda: c
        try:
            with patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False), \
                    patch("finance_copilot.api.routes.annotations.embed_transactions"):
                token = _label(TestClient(app), ["t"], "Health").json()["undo_token"]
            other = sqlite3.connect(path, timeout=0, check_same_thread=False)
            attempts = []

            from finance_copilot.db.queries import review_undo

            real = review_undo._write_annotation

            def probe(conn, tid, row):
                try:
                    other.execute("UPDATE annotations SET category = 'Travel' WHERE transaction_id = 't'")
                    other.commit()
                    attempts.append("slipped in")
                except sqlite3.OperationalError:
                    attempts.append("blocked")
                real(conn, tid, row)

            with patch.object(review_undo, "_write_annotation", probe):
                assert _undo(TestClient(app), token).status_code == 200
            assert attempts == ["blocked"]
        finally:
            app.dependency_overrides.pop(api_get_db, None)


class TestApplyToSimilarCanBeUndone:
    def test_it_returns_a_token_that_restores_the_targets_and_their_counters(self, client, conn):
        _pay(conn, "donor", label="Health", source="manual", confidence=1.0, payee="DONOR", day=1)
        _pay(conn, "t1", payee="SIM ONE", label="Shopping", day=2)
        _pay(conn, "t2", payee="SIM TWO", label="Shopping", day=3)
        before = _state(conn)
        with patch("finance_copilot.api.routes.annotations._embed_in_background"):
            resp = client.post("/api/annotations/a_donor/apply-to-similar", json={"transaction_ids": ["t1", "t2"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] == 2
        assert conn.execute("SELECT category FROM annotations WHERE transaction_id = 't1'").fetchone()[0] == "Health"
        assert _undo(client, resp.json()["undo_token"]).status_code == 200
        assert _state(conn) == before
