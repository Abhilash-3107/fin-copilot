"""Final review of R3 part C: the candidate queries seek by index, a payment waits on a
pair only when its label is none or Transfers, an Uncategorized list says why a row
waits, a move says when it released a hand-made pair, undo refuses a link inside one
account."""
from __future__ import annotations

import re

import pytest

from finance_copilot.db.queries.common import unsorted_sql
from finance_copilot.db.queries.transfers import candidate_pairs_sql, has_open_candidate_sql
from finance_copilot.tests import test_pairing as _unit
from finance_copilot.tests.test_pairing import _in, _out

conn = _unit.conn


def _plan(conn, sql, params=()):
    return [tuple(r)[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


def _scans(lines):
    """A full scan of a transactions alias (the queries alias it t, o, d or c)."""
    return [line for line in lines if re.match(r"SCAN (t|o|d|c)\b", line)]


def _seeks_by_cents(lines, alias):
    return any(line.startswith(f"SEARCH {alias} USING INDEX idx_txn_cents") for line in lines)


class TestTheCandidateQueriesSeekByIndex:
    def test_the_ids_path_scans_nothing(self, conn):
        sql, params = candidate_pairs_sql(ids=["a", "b"])
        lines = _plan(conn, sql, params)
        assert _scans(lines) == []
        assert _seeks_by_cents(lines, "c") and _seeks_by_cents(lines, "d")

    def test_the_between_path_scans_nothing(self, conn):
        sql, params = candidate_pairs_sql(between=("a", "b"))
        assert _scans(_plan(conn, sql, params)) == []

    def test_the_whole_ledger_path_scans_one_side_and_seeks_the_other(self, conn):
        sql, params = candidate_pairs_sql()
        lines = _plan(conn, sql, params)
        assert len(_scans(lines)) == 1
        assert _seeks_by_cents(lines, "c") or _seeks_by_cents(lines, "d")

    def test_waiting_on_a_pair_scans_only_the_row_asking(self, conn):
        lines = _plan(conn, f"SELECT t.id FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id WHERE {unsorted_sql('a')}")
        assert len(_scans(lines)) == 1
        assert _seeks_by_cents(lines, "o")

    def test_has_open_candidate_seeks_the_other_side(self, conn):
        lines = _plan(conn, f"SELECT t.id FROM transactions t WHERE {has_open_candidate_sql('t')}")
        assert len(_scans(lines)) == 1
        assert _seeks_by_cents(lines, "o")

    def test_the_ids_path_still_finds_a_candidate_from_either_side(self, conn):
        from finance_copilot.db.queries.transfers import candidate_pairs

        _out(conn, "d")
        _in(conn, "c")
        assert candidate_pairs(conn, ids=["d"]) == [("d", "c", 0)]
        assert candidate_pairs(conn, ids=["c"]) == [("d", "c", 0)]
        assert candidate_pairs(conn, ids=["c", "d"]) == [("d", "c", 0)]
        assert candidate_pairs(conn, ids=["zz"]) == []


class TestOnlyATransfersGuessWaitsOnAPair:
    """A payment is held back for an own-account candidate only when its label is
    Transfers (or it has none, which is unsorted already)."""

    def _unsorted(self, conn):
        return {
            r["id"] for r in conn.execute(
                f"SELECT t.id FROM transactions t LEFT JOIN annotations a ON a.transaction_id = t.id WHERE {unsorted_sql('a')}"
            )
        }

    @pytest.mark.parametrize("label", [
        ("llm", "Food & Dining", "Restaurants"),
        ("rule", "Investments", "Mutual Funds"),
        ("rag_direct", "Shopping", None),
        ("learned_rule", "Income", "Salary"),
    ])
    def test_an_ordinary_confident_label_is_kept_next_to_a_same_amount_credit(self, conn, label):
        _out(conn, "d", amount=500.0, description="UPI/SWIGGY/1/UPI", ann=label)
        _in(conn, "c", amount=500.0, day=29)
        assert self._unsorted(conn) == {"c"}

    def test_a_transfers_guess_with_a_candidate_waits(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("rule", "Transfers", "Peer Transfer"))
        _in(conn, "c")
        assert self._unsorted(conn) == {"d", "c"}

    def test_a_transfers_guess_without_a_candidate_stays_sorted(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("rule", "Transfers", "Peer Transfer"))
        _in(conn, "c", day=10)
        assert self._unsorted(conn) == {"c"}

    @pytest.mark.parametrize("source", ["manual", "imported", "user_rule"])
    def test_the_users_own_transfers_label_is_never_held_back(self, conn, source):
        _out(conn, "d", description="NEFT/RAMESH", ann=(source, "Transfers", "Peer Transfer"))
        _in(conn, "c")
        assert self._unsorted(conn) == {"c"}

    def test_no_label_and_uncategorized_are_unsorted_with_or_without_a_candidate(self, conn):
        _out(conn, "d1", amount=100.0)
        _out(conn, "d2", amount=200.0, ann=("llm", "Uncategorized"))
        _in(conn, "c1", amount=100.0)
        assert self._unsorted(conn) == {"d1", "d2", "c1"}

    def test_a_self_transfers_label_is_not_held_back(self, conn):
        _out(conn, "d", description="NEFT/RAMESH", ann=("rule", "Self Transfers", "Self Transfer"))
        _in(conn, "c", ann=("rule", "Self Transfers", "Self Transfer"))
        assert self._unsorted(conn) == set()
