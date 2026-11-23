"""The spend offsets (Money Map refund netting) are memoised, and the memo must notice a change
made on another connection: every request opens its own connection, so a fingerprint that only
counts this connection's changes never moves between requests and the Money Map would go stale
until the server restarts."""
from __future__ import annotations

import pytest
import ulid
from fastapi.testclient import TestClient

from finance_copilot.db import connection as dbc
from finance_copilot.db.queries import insights


@pytest.fixture
def client(tmp_path):
    from finance_copilot.api.deps import get_db
    from finance_copilot.main import app

    path = str(tmp_path / "finance.db")
    setup = dbc.get_migrated_db(path)
    setup.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('acc', 'kotak', 'XXXX1111')")
    setup.execute(
        "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_id) "
        "VALUES ('st', 'kotak', '1', '2026-10', 'acc')"
    )

    def add(txn_id, day, amount, dc, category, subcategory=None):
        setup.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES (?, 'st', ?, ?, ?, 'X')", (txn_id, day, amount, dc),
        )
        setup.execute(
            "INSERT INTO annotations (id, transaction_id, category, subcategory, confidence, source) "
            "VALUES (?, ?, ?, ?, 0.9, 'manual')", (f"a_{txn_id}", txn_id, category, subcategory),
        )

    add("shoes", "2026-10-05", 3000, "debit", "Shopping")
    add("refund", "2026-10-20", 500, "credit", "Income", "Refund")
    add("anchor", "2026-10-01", 10, "debit", "Food & Dining")
    setup.execute("INSERT INTO transaction_links (id, txn_a, txn_b, link_type) VALUES (?, 'refund', 'shoes', 'refund')", (str(ulid.ULID()),))
    setup.commit()

    def per_request():
        conn = dbc.get_connection(path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = per_request
    insights._offsets_cache.clear()
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    setup.close()
    insights._offsets_cache.clear()


def _category(client, name):
    categories = client.get("/api/insights", params={"month": "2026-10"}).json()["categories"]
    return next((c for c in categories if c["category"] == name), None)


def test_a_category_change_moves_the_offset_on_the_next_request(client):
    shopping = _category(client, "Shopping")
    assert (shopping["gross"], shopping["offsets"], shopping["net"]) == (3000, 500, 2500)
    # Same refund, same link: only the refunded debit's category changes, through the route (its own connection).
    assert client.patch("/api/annotations/a_shoes", json={"category": "Entertainment", "subcategory": None}).status_code == 200
    assert _category(client, "Shopping") is None
    entertainment = _category(client, "Entertainment")
    assert (entertainment["gross"], entertainment["offsets"], entertainment["net"]) == (3000, 500, 2500)


def test_unlinking_a_refund_moves_the_offset_on_the_next_request(client, tmp_path):
    assert _category(client, "Shopping")["offsets"] == 500
    other = dbc.get_connection(str(tmp_path / "finance.db"))
    other.execute("DELETE FROM transaction_links")
    other.commit()
    other.close()
    assert _category(client, "Shopping")["offsets"] == 0
