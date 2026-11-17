"""Migration 041: every statement of a 0.2.0-style ledger lands in an account.

The ledgers here are built the 0.2.0 way, with migrations up to 040 only, then
migrated by this build. The grouping follows the rule `_balance_series` used
before accounts existed, so no existing ledger splinters.
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from finance_copilot.db import connection
from finance_copilot.db.connection import get_connection, init_db


@pytest.fixture
def ledger_040(tmp_path, monkeypatch):
    """A ledger migrated as far as 0.2.0 went, plus a way to finish the job."""
    old = tmp_path / "migrations_040"
    old.mkdir()
    for path in sorted(connection.MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "041":
            shutil.copy(path, old / path.name)
    real_dir = connection.MIGRATIONS_DIR
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", old)
    conn = get_connection(str(tmp_path / "ledger.db"))
    init_db(conn)

    class Ledger:
        def __init__(self):
            self.conn = conn

        def statement(self, stmt_id, bank, ref=None, month="2026-01"):
            conn.execute(
                "INSERT INTO statements (id, bank_name, parser_version, statement_month, account_ref) "
                "VALUES (?, ?, '1', ?, ?)",
                (stmt_id, bank, month, ref),
            )
            conn.commit()

        def migrate(self):
            monkeypatch.setattr(connection, "MIGRATIONS_DIR", real_dir)
            init_db(conn)
            conn.commit()
            return self

        def accounts_of(self):
            """{statement_id: (bank_name, account_ref)} through the statement's account."""
            rows = conn.execute(
                "SELECT s.id, a.id AS aid, a.bank_name, a.account_ref FROM statements s "
                "JOIN accounts a ON a.id = s.account_id"
            ).fetchall()
            return {r["id"]: (r["aid"], r["bank_name"], r["account_ref"]) for r in rows}

    return Ledger()


def _groups(mapping):
    """The statements grouped by account, as a set of frozensets of statement ids."""
    by_account: dict[str, set[str]] = {}
    for stmt_id, (aid, _bank, _ref) in mapping.items():
        by_account.setdefault(aid, set()).add(stmt_id)
    return {frozenset(ids) for ids in by_account.values()}


def test_the_migration_creates_the_table_and_the_columns(ledger_040):
    ledger_040.migrate()
    columns = {r["name"] for r in ledger_040.conn.execute("PRAGMA table_info(accounts)")}
    assert columns == {"id", "bank_name", "account_ref", "name", "created_at"}
    statement_columns = {r["name"] for r in ledger_040.conn.execute("PRAGMA table_info(statements)")}
    assert {"account_id", "printed_start", "printed_end"} <= statement_columns


def test_an_empty_ledger_gets_no_accounts(ledger_040):
    ledger_040.migrate()
    assert ledger_040.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_one_bank_with_one_number_is_one_account(ledger_040):
    for i, month in enumerate(["2026-01", "2026-02", "2026-03"]):
        ledger_040.statement(f"s{i}", "kotak", "3250508074", month)
    mapping = ledger_040.migrate().accounts_of()
    assert _groups(mapping) == {frozenset({"s0", "s1", "s2"})}
    assert {v[1:] for v in mapping.values()} == {("kotak", "3250508074")}


def test_a_bank_whose_older_statements_had_no_number_stays_one_account(ledger_040):
    ledger_040.statement("old1", "kotak", None, "2025-11")
    ledger_040.statement("old2", "kotak", "", "2025-12")
    ledger_040.statement("new1", "kotak", "3250508074", "2026-01")
    mapping = ledger_040.migrate().accounts_of()
    assert _groups(mapping) == {frozenset({"old1", "old2", "new1"})}
    assert mapping["old1"][2] == "3250508074"


def test_a_bank_with_no_numbers_at_all_is_one_account_without_a_number(ledger_040):
    ledger_040.statement("a", "generic", None, "2026-01")
    ledger_040.statement("b", "generic", "", "2026-02")
    mapping = ledger_040.migrate().accounts_of()
    assert _groups(mapping) == {frozenset({"a", "b"})}
    assert mapping["a"][2] is None


def test_two_numbers_and_an_unknown_make_three_accounts(ledger_040):
    ledger_040.statement("a1", "kotak", "1111111111", "2026-01")
    ledger_040.statement("a2", "kotak", "1111111111", "2026-02")
    ledger_040.statement("b1", "kotak", "2222222222", "2026-01")
    ledger_040.statement("u1", "kotak", None, "2025-12")
    ledger_040.statement("u2", "kotak", "", "2025-11")
    mapping = ledger_040.migrate().accounts_of()
    assert _groups(mapping) == {frozenset({"a1", "a2"}), frozenset({"b1"}), frozenset({"u1", "u2"})}
    assert mapping["a1"][2] == "1111111111"
    assert mapping["b1"][2] == "2222222222"
    assert mapping["u1"][2] is None


def test_two_numbers_and_no_unknown_make_two_accounts(ledger_040):
    ledger_040.statement("a1", "kotak", "1111111111")
    ledger_040.statement("b1", "kotak", "2222222222")
    assert len(_groups(ledger_040.migrate().accounts_of())) == 2


def test_banks_never_share_an_account(ledger_040):
    ledger_040.statement("k1", "kotak", "1111111111")
    ledger_040.statement("h1", "hdfc (auto)", "1111111111")
    ledger_040.statement("g1", "generic", None)
    assert len(_groups(ledger_040.migrate().accounts_of())) == 3


def test_the_automatic_marker_is_not_part_of_the_bank(ledger_040):
    """A bank read by the generic parser is stored as 'hdfc (auto)'; its account is just 'hdfc'."""
    ledger_040.statement("h1", "hdfc (auto)", "9999")
    ledger_040.statement("h2", "hdfc", "9999", "2026-02")
    mapping = ledger_040.migrate().accounts_of()
    assert _groups(mapping) == {frozenset({"h1", "h2"})}
    assert mapping["h1"][1] == "hdfc"


def test_a_renamed_bank_is_its_own_bank(ledger_040):
    """The old statement rename wrote free text into bank_name: it groups as it stands."""
    ledger_040.statement("k1", "kotak", "3250508074", "2026-01")
    ledger_040.statement("k2", "Kotak savings", "3250508074", "2026-02")
    ledger_040.statement("k3", "Kotak savings", None, "2026-03")
    mapping = ledger_040.migrate().accounts_of()
    assert _groups(mapping) == {frozenset({"k1"}), frozenset({"k2", "k3"})}
    assert mapping["k2"][1] == "Kotak savings"


def test_no_statement_is_left_without_an_account_and_no_account_is_empty(ledger_040):
    ledger_040.statement("a", "kotak", "1")
    ledger_040.statement("b", "kotak", "2")
    ledger_040.statement("c", "kotak", None)
    ledger_040.statement("d", "weird bank", None)
    conn = ledger_040.migrate().conn
    assert conn.execute("SELECT COUNT(*) FROM statements WHERE account_id IS NULL").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM accounts WHERE id NOT IN (SELECT account_id FROM statements)"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE name IS NOT NULL").fetchone()[0] == 0


def test_the_account_ids_do_not_depend_on_the_run(ledger_040, tmp_path):
    """Derived from the statements, so migrating the same ledger twice names the accounts the same."""
    ledger_040.statement("s1", "kotak", "1")
    ledger_040.statement("s2", "kotak", "2")
    ids = sorted(v[0] for v in ledger_040.migrate().accounts_of().values())
    assert ids == ["acc_s1", "acc_s2"]


def test_the_account_number_is_unique_within_a_bank(ledger_040):
    ledger_040.statement("s1", "kotak", "1")
    conn = ledger_040.migrate().conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('x', 'kotak', '1')")
    # An unknown number is not a number: any count of those may exist.
    conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('y', 'kotak', NULL)")
    conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('z', 'kotak', NULL)")


def test_the_printed_period_starts_empty(ledger_040):
    ledger_040.statement("s", "kotak", "1")
    row = ledger_040.migrate().conn.execute("SELECT printed_start, printed_end FROM statements").fetchone()
    assert (row["printed_start"], row["printed_end"]) == (None, None)
