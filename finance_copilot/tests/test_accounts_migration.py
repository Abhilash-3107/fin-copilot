"""Migration 041: every statement of a 0.2.0-style ledger lands in an account.

The ledgers here are built the 0.2.0 way, with migrations up to 040 only, then
migrated by this build. The grouping follows the rule `_balance_series` used
before accounts existed, so no existing ledger splinters.
"""
from __future__ import annotations

import shutil
from pathlib import Path

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


def test_a_state_bank_read_by_the_generic_parser_is_sbi_with_no_name(ledger_040):
    """0.1.2's generic parser wrote the text it matched, "state bank (auto)": a key for SBI."""
    ledger_040.statement("s1", "state bank (auto)", "3250508074")
    ledger = ledger_040.migrate()
    mapping = ledger.accounts_of()
    assert mapping["s1"][1:] == ("sbi", "3250508074")
    assert set(_names(ledger.conn).values()) == {None}


def test_state_bank_and_sbi_with_one_number_are_one_account(ledger_040):
    ledger_040.statement("s1", "state bank (auto)", "3250508074", "2026-01")
    ledger_040.statement("s2", "sbi (auto)", "3250508074", "2026-02")
    ledger_040.statement("s3", "sbi", "3250508074", "2026-03")
    ledger = ledger_040.migrate()
    assert _groups(ledger.accounts_of()) == {frozenset({"s1", "s2", "s3"})}
    assert {v[1] for v in ledger.accounts_of().values()} == {"sbi"}
    assert set(_names(ledger.conn).values()) == {None}


def test_a_marked_bank_the_list_does_not_know_is_still_a_key_and_text_on_its_number_joins_it(ledger_040):
    ledger_040.statement("a", "foo bank (auto)", "777", "2026-01")
    ledger_040.statement("b", "My salary", "777", "2026-02")
    ledger = ledger_040.migrate()
    mapping = ledger.accounts_of()
    assert _groups(mapping) == {frozenset({"a", "b"})}
    assert mapping["a"][1] == "foo bank"
    assert set(_names(ledger.conn).values()) == {"My salary"}


def _names(conn):
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM accounts")}


def test_text_typed_on_a_real_banks_number_joins_that_bank_and_names_the_account(ledger_040):
    """The old statement rename wrote free text into bank_name; the same number says which bank it is."""
    ledger_040.statement("k1", "kotak", "3250508074", "2026-01")
    ledger_040.statement("k2", "Kotak salary", "3250508074", "2026-02")
    ledger = ledger_040.migrate()
    mapping = ledger.accounts_of()
    assert _groups(mapping) == {frozenset({"k1", "k2"})}
    assert mapping["k2"][1] == "kotak"
    assert set(_names(ledger.conn).values()) == {"Kotak salary"}


def test_typed_text_without_a_number_has_nothing_to_fold_on(ledger_040):
    """No number, no way to tell which bank: it stays apart (as typed), and the user can move it."""
    ledger_040.statement("k1", "kotak", "3250508074", "2026-01")
    ledger_040.statement("k3", "Kotak salary", None, "2026-03")
    ledger = ledger_040.migrate()
    assert _groups(ledger.accounts_of()) == {frozenset({"k1"}), frozenset({"k3"})}


def test_the_most_recent_typed_text_names_the_account(ledger_040):
    ledger_040.statement("k1", "kotak", "3250508074", "2026-01")
    ledger_040.statement("k2", "Old name", "3250508074", "2026-02")
    ledger_040.statement("k3", "Salary account", "3250508074", "2026-03")
    ledger = ledger_040.migrate()
    assert set(_names(ledger.conn).values()) == {"Salary account"}


def test_typed_text_with_no_matching_number_is_its_own_bank_named_as_typed(ledger_040):
    ledger_040.statement("k1", "kotak", "3250508074", "2026-01")
    ledger_040.statement("k2", "Kotak savings", "1111111111", "2026-02")
    ledger_040.statement("k3", "Kotak savings", None, "2026-03")
    ledger = ledger_040.migrate()
    mapping = ledger.accounts_of()
    assert _groups(mapping) == {frozenset({"k1"}), frozenset({"k2", "k3"})}
    assert mapping["k2"][1] == "kotak savings"
    assert _names(ledger.conn)[mapping["k2"][0]] == "Kotak savings"
    assert _names(ledger.conn)[mapping["k1"][0]] is None


def test_every_statement_renamed_keeps_its_text_as_the_name(ledger_040):
    """All of a bank's statements renamed "Kotak salary": still one account, called what they typed."""
    for i in range(3):
        ledger_040.statement(f"k{i}", "Kotak salary", "3250508074", f"2026-0{i + 1}")
    ledger = ledger_040.migrate()
    assert len(_groups(ledger.accounts_of())) == 1
    assert set(_names(ledger.conn).values()) == {"Kotak salary"}


def test_bank_keys_group_ignoring_case_and_spaces(ledger_040):
    """"Kotak" beside "kotak" with the same number, and "kotak " with a trailing space, are one bank."""
    ledger_040.statement("a", "Kotak", "3250508074", "2026-01")
    ledger_040.statement("b", "kotak", "3250508074", "2026-02")
    ledger_040.statement("c", "kotak ", "3250508074", "2026-03")
    ledger_040.statement("d", "KOTAK (auto)", None, "2026-04")
    ledger = ledger_040.migrate()
    mapping = ledger.accounts_of()
    assert _groups(mapping) == {frozenset({"a", "b", "c", "d"})}
    assert {v[1] for v in mapping.values()} == {"kotak"}
    assert set(_names(ledger.conn).values()) == {None}


def test_an_upper_case_automatic_marker_is_not_part_of_the_bank(ledger_040):
    ledger_040.statement("a", "Foo (AUTO)", None, "2026-01")
    ledger_040.statement("b", "Foo", None, "2026-02")
    ledger = ledger_040.migrate()
    mapping = ledger.accounts_of()
    assert _groups(mapping) == {frozenset({"a", "b"})}
    assert mapping["a"][1] == "foo"
    # "Foo" without the marker is what someone typed; the marked one is a parser's key.
    assert set(_names(ledger.conn).values()) == {"Foo"}


def test_a_marked_bank_name_alone_is_a_parser_key_and_names_nothing(ledger_040):
    ledger_040.statement("a", "Foo (AUTO)", None, "2026-01")
    ledger = ledger_040.migrate()
    assert set(_names(ledger.conn).values()) == {None}


def test_a_blank_bank_name_is_a_bank_called_bank_with_no_name(ledger_040):
    from finance_copilot.db.queries.accounts import account_labels

    ledger_040.statement("a", "", None, "2026-01")
    ledger_040.statement("b", "  ", None, "2026-02")
    ledger_040.statement("c", " (auto)", None, "2026-03")
    ledger = ledger_040.migrate()
    assert len(_groups(ledger.accounts_of())) == 1
    assert set(_names(ledger.conn).values()) == {None}
    assert set(account_labels(ledger.conn).values()) == {"Bank"}


def test_a_blank_bank_name_with_a_known_number_joins_that_bank(ledger_040):
    ledger_040.statement("a", "kotak", "3250508074", "2026-01")
    ledger_040.statement("b", "", "3250508074", "2026-02")
    ledger = ledger_040.migrate()
    assert _groups(ledger.accounts_of()) == {frozenset({"a", "b"})}
    assert set(_names(ledger.conn).values()) == {None}


def test_the_sql_list_of_banks_is_the_list_the_parsers_use(ledger_040):
    """Typed text on a bank's number folds into it, for every bank key. A key missing from
    the migration's list would not be a bank there and the text would stay apart."""
    from finance_copilot.parsers.bank_identity import CANONICAL_BANKS

    for i, bank in enumerate(sorted(CANONICAL_BANKS)):
        ledger_040.statement(f"b{i}", bank, f"NUM{i}", "2026-01")
        ledger_040.statement(f"t{i}", "typed", f"NUM{i}", "2026-02")
    mapping = ledger_040.migrate().accounts_of()
    assert len(_groups(mapping)) == len(CANONICAL_BANKS)


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
    # Only text the user typed becomes a name: "weird bank" was one.
    assert [r["name"] for r in conn.execute("SELECT name FROM accounts WHERE name IS NOT NULL")] == ["weird bank"]


def test_the_account_ids_do_not_depend_on_the_run(ledger_040, tmp_path):
    """Derived from the statements, so migrating the same ledger twice names the accounts the same."""
    ledger_040.statement("s1", "kotak", "1")
    ledger_040.statement("s2", "kotak", "2")
    ids = sorted(v[0] for v in ledger_040.migrate().accounts_of().values())
    assert ids == ["acc_s1", "acc_s2"]


def test_a_number_may_hold_several_accounts_once_the_user_splits_it(ledger_040):
    """A statement moved to a new account leaves two with the same bank and number."""
    ledger_040.statement("s1", "kotak", "1")
    conn = ledger_040.migrate().conn
    conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('x', 'kotak', '1')")
    conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('y', 'kotak', NULL)")
    conn.execute("INSERT INTO accounts (id, bank_name, account_ref) VALUES ('z', 'kotak', NULL)")


def test_the_printed_period_starts_empty(ledger_040):
    ledger_040.statement("s", "kotak", "1")
    row = ledger_040.migrate().conn.execute("SELECT printed_start, printed_end FROM statements").fetchone()
    assert (row["printed_start"], row["printed_end"]) == (None, None)


@pytest.mark.parametrize(
    "shape",
    [
        [("kotak", "1111", 1), ("kotak", "1111", 2)],
        [("kotak", None, 1), ("kotak", "1111", 2), ("kotak", None, 3)],
        [("kotak", "1111", 1), ("kotak", "2222", 2), ("kotak", None, 3), ("kotak", None, 4)],
        [("kotak", "1111", 1), ("hdfc (auto)", "2222", 2), ("kotak", None, 3), ("hdfc", None, 4)],
        [("kotak", None, 1), ("kotak", None, 2)],
    ],
)
def test_a_ledger_built_by_uploading_groups_the_statements_as_the_migration_does(ledger_040, shape, monkeypatch):
    """The same statements, once uploaded one by one and once migrated together, end up in
    the same accounts: ingest and the migration are one rule, not two."""
    from finance_copilot.tests.test_accounts import _FakeParser, _ingest, _make_conn, _rows

    # The fixture holds migrations at 040; the uploading side runs the whole set.
    real = connection.MIGRATIONS_DIR
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", Path(connection.__file__).parent / "migrations")
    live = _make_conn()
    monkeypatch.setattr(connection, "MIGRATIONS_DIR", real)
    for bank, ref, month in shape:
        _ingest(live, _FakeParser(bank, ref, _rows(10000, 1, month=month)), f"m{month}")
    built: dict[str, set[str]] = {}
    for row in live.execute("SELECT statement_month, account_id FROM statements"):
        built.setdefault(row["account_id"], set()).add(row["statement_month"])

    for bank, ref, month in shape:
        ledger_040.statement(f"m{month}", bank, ref, f"2026-{month:02d}")
    migrated = _groups(ledger_040.migrate().accounts_of())

    assert {frozenset(months) for months in built.values()} == {
        frozenset(f"2026-{m[1:].zfill(2)}" for m in ids) for ids in migrated
    }
