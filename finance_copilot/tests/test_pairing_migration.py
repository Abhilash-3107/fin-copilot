"""Migration 044: annotations accept source 'pair', every existing row survives
as it was, and the unpaired table only takes an ordered pair."""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from finance_copilot.db import connection
from finance_copilot.db.connection import get_connection, init_db


@pytest.fixture
def ledger_043(tmp_path, monkeypatch):
    old = tmp_path / "migrations_043"
    old.mkdir()
    for path in sorted(connection.MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "044":
            shutil.copy(path, old / path.name)
    real_dir = connection.MIGRATIONS_DIR
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", old)
    conn = get_connection(str(tmp_path / "ledger.db"))
    init_db(conn)
    conn.execute("INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES ('s', 'kotak', '1', '2026-10')")
    for i, source in enumerate(["manual", "llm", "rag_direct", "policy", "learned_rule", "rule", "user_rule", "imported"]):
        conn.execute(
            "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
            "VALUES (?, 's', '2026-10-01', ?, 'debit', 'X')", (f"t{i}", 10 + i))
        conn.execute(
            "INSERT INTO annotations (id, transaction_id, merchant, category, subcategory, tags, confidence, source, "
            "original_source, category_id, subcategory_id, reasoning) "
            "VALUES (?, ?, 'M', 'Self Transfers', 'Self Transfer', '[\"a\"]', 0.7, ?, 'llm', 'cat_self_transfers', "
            "'cat_tx_self', '{\"why\": 1}')", (f"a{i}", f"t{i}", source))
    conn.commit()
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", real_dir)
    return conn


def _rows(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM annotations ORDER BY id")]


def test_every_annotation_survives_unchanged_column_for_column(ledger_043):
    before = _rows(ledger_043)
    init_db(ledger_043)
    assert _rows(ledger_043) == before
    assert [c[1] for c in ledger_043.execute("PRAGMA table_info(annotations)")] == [
        "id", "transaction_id", "merchant", "category", "subcategory", "tags", "confidence", "source",
        "annotated_at", "original_source", "category_id", "subcategory_id", "reasoning",
    ]


def test_the_category_index_and_the_one_annotation_per_payment_rule_survive(ledger_043):
    init_db(ledger_043)
    assert ledger_043.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_annotations_category'").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        ledger_043.execute(
            "INSERT INTO annotations (id, transaction_id, category, source) VALUES ('dup', 't0', 'X', 'manual')")


def test_pair_is_now_an_allowed_source_and_an_unknown_one_still_is_not(ledger_043):
    init_db(ledger_043)
    ledger_043.execute("INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description) "
                       "VALUES ('p', 's', '2026-10-01', 1, 'debit', 'X')")
    ledger_043.execute("INSERT INTO annotations (id, transaction_id, category, source) VALUES ('ap', 'p', 'Self Transfers', 'pair')")
    with pytest.raises(sqlite3.IntegrityError):
        ledger_043.execute("INSERT INTO annotations (id, transaction_id, category, source) VALUES ('ax', 't1', 'X', 'bogus')")


def test_the_unpaired_table_takes_only_an_ordered_pair(ledger_043):
    init_db(ledger_043)
    ledger_043.execute("INSERT INTO transfer_unpaired (txn_a, txn_b) VALUES ('a', 'b')")
    with pytest.raises(sqlite3.IntegrityError):
        ledger_043.execute("INSERT INTO transfer_unpaired (txn_a, txn_b) VALUES ('b', 'a')")
    with pytest.raises(sqlite3.IntegrityError):
        ledger_043.execute("INSERT INTO transfer_unpaired (txn_a, txn_b) VALUES ('a', 'b')")


def test_a_ledger_with_no_annotations_migrates(tmp_path):
    conn = get_connection(str(tmp_path / "fresh.db"))
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 0
