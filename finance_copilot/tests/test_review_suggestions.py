"""Suggestions on the review queue (R2 part C): up to three, each with a plain
reason, best first, never Uncategorized, computed in a bounded number of queries."""
from __future__ import annotations

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
    yield TestClient(app)
    app.dependency_overrides.pop(api_get_db, None)


def _pay(conn, txn_id, payee="CHAI WALA", label=None, *, sub=None, source="manual", confidence=1.0, dc="debit", day=1):
    conn.execute(
        "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key, upi_meta) "
        "VALUES (?, 's', ?, 100, ?, ?, ?, ?)",
        (txn_id, f"2026-06-{day:02d}", dc, f"UPI/{payee}/12345678{day:04d}/Tea money", payee, '{"note": "Tea money"}'),
    )
    if label is not None:
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"a_{txn_id}", txn_id, label, sub, confidence, source),
        )
    conn.commit()


def _history(conn, payee, label, n, *, sub=None, source="manual", dc="debit", start=0):
    for i in range(n):
        _pay(conn, f"h_{payee}_{label}_{sub}_{dc}_{start + i}", payee, label, sub=sub, source=source, dc=dc, day=2 + (start + i) % 25)


def _suggestions(client, txn_id="q"):
    items = {i["transaction_id"]: i for i in client.get("/api/annotations/review-queue").json()}
    return items[txn_id]["suggestions"]


def _ranked(suggestions):
    return [(s["category"], s["subcategory"]) for s in suggestions]


class TestHistory:
    def test_the_payees_verified_labels_come_first_with_their_count(self, client, conn):
        _history(conn, "CHAI WALA", "Shopping", 3, sub="Groceries")
        _history(conn, "CHAI WALA", "Entertainment", 1)
        _pay(conn, "q", "CHAI WALA", day=30)
        got = _suggestions(client)
        assert _ranked(got) == [("Shopping", "Groceries"), ("Entertainment", None)]
        assert got[0]["reason"] == "You chose this 3 times for CHAI WALA"
        assert got[1]["reason"] == "You chose this once for CHAI WALA"

    def test_only_verified_labels_in_the_same_direction_count(self, client, conn):
        _history(conn, "CHAI WALA", "Shopping", 5, source="llm")
        _history(conn, "CHAI WALA", "Income", 4, dc="credit")
        _history(conn, "OTHER PAYEE", "Entertainment", 4)
        _pay(conn, "q", "CHAI WALA", day=30)
        assert _suggestions(client) == []

    def test_uncategorized_is_never_suggested(self, client, conn):
        _history(conn, "CHAI WALA", "Uncategorized", 6)
        _history(conn, "CHAI WALA", "Shopping", 1)
        _pay(conn, "q", "CHAI WALA", "Uncategorized", source="llm", confidence=0.4, day=30)
        assert _ranked(_suggestions(client)) == [("Shopping", None)]


class TestMachineGuess:
    @pytest.mark.parametrize(
        ("source", "reason"),
        [
            ("llm", "AI guess, 68% sure"),
            ("model", "AI guess, 68% sure"),
            ("rag_prompted", "AI guess, 68% sure"),
            ("rag_direct", "Similar to past payments"),
            ("learned_rule", "Remembered payee"),
            ("policy", "Your rule for small payments"),
        ],
    )
    def test_the_guess_says_where_it_came_from(self, client, conn, source, reason):
        _pay(conn, "q", "CHAI WALA", "Shopping", source=source, confidence=0.68)
        assert _suggestions(client) == [{"category": "Shopping", "subcategory": None, "reason": reason}]

    @pytest.mark.parametrize(
        ("source", "reason"),
        [("user_rule", "Your rule"), ("imported", "Imported label"), ("something_new", "Current label")],
    )
    def test_every_source_has_a_neutral_reason_not_a_wrong_one(self, source, reason):
        from finance_copilot.pipeline.suggestions import _guess_reason

        assert _guess_reason({"source": source, "confidence": 0.68}) == reason

    def test_a_guess_of_uncategorized_is_not_offered(self, client, conn):
        _pay(conn, "q", "CHAI WALA", "Uncategorized", source="llm", confidence=0.9)
        assert _suggestions(client) == []

    def test_a_bare_payment_with_no_history_has_no_suggestions(self, client, conn):
        _pay(conn, "q", "CHAI WALA")
        assert _suggestions(client) == []

    def test_a_label_seen_in_history_is_listed_once_with_the_history_reason(self, client, conn):
        _history(conn, "CHAI WALA", "Shopping", 2, sub="Groceries")
        _pay(conn, "q", "CHAI WALA", "Shopping", sub="Groceries", source="llm", confidence=0.6, day=30)
        got = _suggestions(client)
        assert [(s["category"], s["subcategory"]) for s in got] == [("Shopping", "Groceries")]
        assert got[0]["reason"] == "You chose this 2 times for CHAI WALA"


class TestCap:
    def test_at_most_three_and_the_guess_keeps_its_place(self, client, conn):
        for n, label in enumerate(["Shopping", "Entertainment", "Health", "Travel"]):
            _history(conn, "CHAI WALA", label, 6 - n, start=n * 6)
        _pay(conn, "q", "CHAI WALA", "Utilities", source="llm", confidence=0.6, day=30)
        got = _suggestions(client)
        assert len(got) == 3
        # Two best-supported labels, then the card's own guess in the last slot.
        assert _ranked(got) == [("Shopping", None), ("Entertainment", None), ("Utilities", None)]

    def test_each_suggestion_is_distinct(self, client, conn):
        _history(conn, "CHAI WALA", "Shopping", 2)
        _history(conn, "CHAI WALA", "Shopping", 1, sub="Groceries", start=10)
        _pay(conn, "q", "CHAI WALA", day=30)
        got = _ranked(_suggestions(client))
        assert len(set(got)) == len(got) == 2


class TestSimilarPayments:
    @staticmethod
    def _embeddings(neighbours):
        """Pretend every payment has a vector and these neighbours are closest."""
        return (
            patch("finance_copilot.pipeline.suggestions.stored_embeddings", side_effect=lambda c, ids: dict.fromkeys(ids, [0.1])),
            patch("finance_copilot.pipeline.suggestions.find_similar", return_value=neighbours),
        )

    def test_the_label_of_the_most_similar_verified_payment_fills_the_gap(self, client, conn):
        _pay(conn, "near", "CHAI POINT", "Shopping", sub="Groceries", day=2)
        _pay(conn, "q", "CHAI WALA", day=30)
        a, b = self._embeddings([{"transaction_id": "near", "distance": 0.1}])
        with a, b:
            got = _suggestions(client)
        assert got == [{"category": "Shopping", "subcategory": "Groceries", "reason": "Similar to CHAI POINT, which you sorted this way"}]

    def test_machine_labelled_and_uncategorized_neighbours_are_ignored(self, client, conn):
        _pay(conn, "guess", "CHAI POINT", "Shopping", source="llm", confidence=0.9, day=2)
        _pay(conn, "uncat", "CHAI HUT", "Uncategorized", day=3)
        _pay(conn, "q", "CHAI WALA", day=30)
        a, b = self._embeddings([
            {"transaction_id": "guess", "distance": 0.1}, {"transaction_id": "uncat", "distance": 0.1},
        ])
        with a, b:
            assert _suggestions(client) == []

    def test_a_neighbour_below_the_similarity_floor_is_ignored(self, client, conn):
        _pay(conn, "far", "BOOK SHOP", "Shopping", day=2)
        _pay(conn, "q", "CHAI WALA", day=30)
        a, b = self._embeddings([{"transaction_id": "far", "distance": 0.7}])
        with a, b:
            assert _suggestions(client) == []

    def test_without_embeddings_the_queue_still_loads(self, client, conn, caplog):
        _pay(conn, "q", "CHAI WALA", "Shopping", source="llm", confidence=0.7)
        import sqlite3

        with patch("finance_copilot.pipeline.suggestions.stored_embeddings",
                   side_effect=sqlite3.OperationalError("no such table: vec_items")), \
                caplog.at_level("WARNING", logger="finance_copilot.pipeline.suggestions"):
            assert _ranked(_suggestions(client)) == [("Shopping", None)]
        assert not caplog.records, "no embeddings is the quiet path"


class TestBoundedQueries:
    def test_the_query_count_does_not_grow_with_the_queue(self, client, conn):
        """Every item has a stored vector and a verified neighbour, so the whole
        similar-payments path runs for each one. The vector searches are one per
        item by design; everything else (history, neighbour labels, payee names)
        is read once for the page, so the count grows by those two per item and no more."""
        from finance_copilot.config import current_embedding_model
        from finance_copilot.db.queries.embeddings import ensure_vec_dimension, upsert_embedding

        ensure_vec_dimension(conn)

        def vec(axis):
            """One payee's payments share a direction; payees are orthogonal."""
            from finance_copilot.config import current_embedding_dim

            v = [0.0] * current_embedding_dim()
            v[axis] = 1.0
            return v

        def statements_for(n: int) -> list[str]:
            conn.execute("DELETE FROM vec_items")
            conn.execute("DELETE FROM embedding_meta")
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM transactions")
            for i in range(n):
                payee = f"PAYEE {i}"
                _pay(conn, f"near{i}", f"NEAR {i}", "Shopping", day=2)
                _pay(conn, f"q{i}", payee, "Entertainment", source="llm", confidence=0.5, day=30)
                upsert_embedding(conn, f"near{i}", vec(i), current_embedding_model(), "h")
                upsert_embedding(conn, f"q{i}", vec(i), current_embedding_model(), "h")
            conn.commit()
            seen: list[str] = []
            conn.set_trace_callback(seen.append)
            try:
                items = client.get("/api/annotations/review-queue").json()
            finally:
                conn.set_trace_callback(None)
            # The vector path really ran: a neighbour's label is offered.
            assert all(any(s["category"] == "Shopping" for s in it["suggestions"]) for it in items), items[:1]
            # sqlite-vec reports its own bookkeeping as "-- ..." statements; they are not ours.
            return [sql for sql in seen if not sql.startswith("--")]

        small, large = statements_for(5), statements_for(60)
        assert any("vec_items" in sql for sql in large), "the vector path did not run"
        # find_similar is two statements (the index search, then its filter) per item.
        assert len(large) <= len(small) + 2 * (60 - 5) + 2, f"{len(small)} statements for 5 payees but {len(large)} for 60"

    def test_unexpected_errors_in_the_similar_search_are_logged(self, client, conn, caplog):
        _pay(conn, "q", "CHAI WALA", "Shopping", source="llm", confidence=0.7)
        with patch("finance_copilot.pipeline.suggestions.stored_embeddings", side_effect=RuntimeError("boom")), \
                caplog.at_level("WARNING", logger="finance_copilot.pipeline.suggestions"):
            assert _ranked(_suggestions(client)) == [("Shopping", None)]
        assert any("boom" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


class TestPayeeAndNote:
    def test_an_item_carries_the_payee_and_the_note_the_user_typed(self, client, conn):
        _pay(conn, "q", "CHAI WALA")
        [item] = client.get("/api/annotations/review-queue").json()
        assert item["payee"] == "CHAI WALA"
        assert item["note"] == "Tea money"

    def test_boilerplate_notes_are_dropped(self, client, conn):
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, counterparty_key) "
            "VALUES ('q', 's', '2026-06-01', 100, 'debit', 'UPI/CHAI WALA/123456789012/UPI', 'CHAI WALA')"
        )
        conn.commit()
        [item] = client.get("/api/annotations/review-queue").json()
        assert item["note"] is None
