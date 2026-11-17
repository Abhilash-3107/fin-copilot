"""Migration 043: what a statement's file held, and whether its balance checked out.

The ledger is built the 0.2.0 way (migrations up to 042), then migrated by this
build. Every statement of it gets the count of its payments as what its file
held and what it added, and the running-balance check of its payments. The
"balanced" figure each statement showed before must not change: it is a stored
fact J15 compares across the upgrade.
"""
from __future__ import annotations

import shutil

import pytest

from finance_copilot.db import connection
from finance_copilot.db.connection import get_connection, init_db
from finance_copilot.db.queries.transactions import statement_balance_checks

# The check as 0.2.0 computed it, from the stored rows, written out here so the
# migrated figures are held against the old code and not against themselves.
_OLD_CHECK = """
WITH chain AS (
    SELECT statement_id, running_balance, amount, debit_credit,
           LAG(running_balance) OVER (PARTITION BY statement_id ORDER BY rowid) AS prev_balance
    FROM transactions
    WHERE running_balance IS NOT NULL
)
SELECT statement_id, COUNT(*) AS checked,
       SUM(CASE WHEN ABS((running_balance - prev_balance)
                         - (CASE WHEN debit_credit = 'credit' THEN amount ELSE -amount END)) <= 0.01
                THEN 1 ELSE 0 END) AS consistent
FROM chain WHERE prev_balance IS NOT NULL AND debit_credit IS NOT NULL
GROUP BY statement_id
"""


@pytest.fixture
def ledger_042(tmp_path, monkeypatch):
    old = tmp_path / "migrations_042"
    old.mkdir()
    for path in sorted(connection.MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "043":
            shutil.copy(path, old / path.name)
    real_dir = connection.MIGRATIONS_DIR
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", old)
    conn = get_connection(str(tmp_path / "ledger.db"))
    init_db(conn)

    class Ledger:
        def __init__(self):
            self.conn = conn
            self.n = 0

        def statement(self, stmt_id, rows):
            """rows: (amount, 'debit'|'credit', running_balance) in the order they were stored."""
            conn.execute(
                "INSERT INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, 'kotak', '1', '2026-05')",
                (stmt_id,),
            )
            for amount, dc, balance in rows:
                self.n += 1
                conn.execute(
                    "INSERT INTO transactions (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance) "
                    "VALUES (?, ?, '2026-05-01', ?, ?, 'x', ?)",
                    (f"t{self.n}", stmt_id, amount, dc, balance),
                )
            conn.commit()

        def old_checks(self):
            out = {}
            for r in conn.execute(_OLD_CHECK):
                out[r["statement_id"]] = {
                    "checked": r["checked"], "consistent": r["consistent"], "balanced": r["checked"] == r["consistent"],
                }
            return out

        def migrate(self):
            monkeypatch.setattr(connection, "MIGRATIONS_DIR", real_dir)
            init_db(conn)
            conn.commit()

    return Ledger()


def _fill(ledger):
    ledger.statement("balanced", [(100, "debit", 900), (50, "credit", 950), (25, "debit", 925)])
    ledger.statement("unbalanced", [(100, "debit", 900), (50, "credit", 500), (25, "debit", 475)])
    ledger.statement("no_balances", [(100, "debit", None), (50, "credit", None)])
    ledger.statement("one_balance", [(100, "debit", 900)])
    ledger.statement("empty", [])
    # A row with no balance inside the chain: the check reads the rows that have one, as 0.2.0 did.
    ledger.statement("gap", [(100, "debit", 900), (10, "debit", None), (50, "credit", 950)])
    # Within a cent of the amount is balanced.
    ledger.statement("cents", [(100, "debit", 900.0), (50.005, "credit", 950.0)])


class TestBackfill:
    def test_every_statement_holds_its_payments_as_what_its_file_had_and_added(self, ledger_042):
        _fill(ledger_042)
        ledger_042.migrate()
        rows = {
            r["id"]: (r["rows_in_file"], r["rows_added"])
            for r in ledger_042.conn.execute("SELECT id, rows_in_file, rows_added FROM statements")
        }
        assert rows == {
            "balanced": (3, 3), "unbalanced": (3, 3), "no_balances": (2, 2), "one_balance": (1, 1),
            "empty": (0, 0), "gap": (3, 3), "cents": (2, 2),
        }

    def test_the_balance_check_is_stored_for_every_statement(self, ledger_042):
        _fill(ledger_042)
        ledger_042.migrate()
        got = {
            r["id"]: (r["balance_checked"], r["balance_consistent"])
            for r in ledger_042.conn.execute("SELECT id, balance_checked, balance_consistent FROM statements")
        }
        assert got == {
            "balanced": (2, 2), "unbalanced": (2, 1), "no_balances": (0, 0), "one_balance": (0, 0),
            "empty": (0, 0), "gap": (1, 1), "cents": (1, 1),
        }

    def test_the_balanced_result_of_every_statement_is_what_it_was_before(self, ledger_042):
        _fill(ledger_042)
        before = ledger_042.old_checks()
        assert before["unbalanced"]["balanced"] is False  # the case has teeth
        ledger_042.migrate()
        after = statement_balance_checks(ledger_042.conn)
        # A statement with no chain was absent before and is reported as balanced with nothing checked.
        for stmt_id in ("balanced", "unbalanced", "gap", "cents"):
            assert after[stmt_id] == before[stmt_id], stmt_id
        for stmt_id in ("no_balances", "one_balance", "empty"):
            assert stmt_id not in before
            assert after.get(stmt_id, {"checked": 0, "consistent": 0, "balanced": True}) == {
                "checked": 0, "consistent": 0, "balanced": True,
            }

    def test_the_list_route_gives_the_same_balance_check_after_the_upgrade(self, ledger_042, monkeypatch):
        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.main import app

        _fill(ledger_042)
        before = ledger_042.old_checks()
        ledger_042.migrate()
        app.dependency_overrides[api_get_db] = lambda: ledger_042.conn
        try:
            shown = {s["id"]: s["balance_check"] for s in TestClient(app).get("/api/statements").json()}
        finally:
            app.dependency_overrides.pop(api_get_db, None)
        for stmt_id, check in before.items():
            assert shown[stmt_id] == check
        assert shown["no_balances"] == {"checked": 0, "consistent": 0, "balanced": True}

    def test_migrating_a_ledger_with_no_statements_is_a_no_op(self, ledger_042):
        ledger_042.migrate()
        assert ledger_042.conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0
        columns = {row[1] for row in ledger_042.conn.execute("PRAGMA table_info(statements)")}
        assert {"rows_in_file", "rows_added", "balance_checked", "balance_consistent"} <= columns
