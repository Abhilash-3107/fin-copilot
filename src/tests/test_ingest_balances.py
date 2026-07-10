"""Ingest balance handling: artifact rows become statement metadata, continuity checks."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.connection import init_db
from src.models.transaction import Transaction
from src.pipeline.ingest import (
    _split_balance_artifacts,
    check_continuity,
    ingest_pdf,
    is_balance_artifact,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def _txn(desc, amount=100.0, dc="credit", balance=None, day=1):
    return Transaction(
        txn_date=date(2026, 5, day), amount=amount, debit_credit=dc,
        raw_description=desc, running_balance=balance,
    )


class TestBalanceArtifactDetection:
    @pytest.mark.parametrize("desc", [
        "OPENING BALANCE ...",
        "Opening Balance",
        "  opening balance",
        "CLOSING BALANCE",
        "Balance B/F",
        "B/F",
        "Balance Brought Forward",
        "Balance Carried Forward",
        "C/F 12,345.00",
    ])
    def test_artifact_rows_detected(self, desc):
        assert is_balance_artifact(_txn(desc))

    @pytest.mark.parametrize("desc", [
        # Interest lines mention "Closing Balance" mid-description; they are real income.
        "Int.Pd:3250508074:01-01-2026 to 31-03-2026 Closing Balance",
        "UPI/OPENING BALANCE STORE/123/UPI",  # merchant name containing the phrase, not anchored
        "UPI/ZOMATO/12345/UPI",
        "NEFT CR SALARY OPENING",
    ])
    def test_real_rows_not_detected(self, desc):
        assert not is_balance_artifact(_txn(desc))


class TestSplitBalanceArtifacts:
    def test_opening_artifact_sets_opening_and_is_removed(self):
        parsed = [
            _txn("OPENING BALANCE ...", amount=65378.0, balance=65378.0),
            _txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit", balance=65178.0),
        ]
        txns, opening, closing = _split_balance_artifacts(parsed)
        assert [t.raw_description for t in txns] == ["UPI/ZOMATO/1/UPI"]
        assert opening == 65378.0
        assert closing == 65178.0

    def test_opening_computed_from_first_txn_when_no_artifact(self):
        parsed = [
            _txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit", balance=65178.0),
            _txn("NEFT CR SALARY", amount=1000.0, dc="credit", balance=66178.0),
        ]
        txns, opening, closing = _split_balance_artifacts(parsed)
        assert len(txns) == 2
        assert opening == pytest.approx(65378.0)  # 65178 + 200 undone debit
        assert closing == 66178.0

    def test_closing_artifact_wins_over_last_txn(self):
        parsed = [
            _txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit", balance=65178.0),
            _txn("CLOSING BALANCE", amount=65178.0, balance=65178.5),
        ]
        txns, _, closing = _split_balance_artifacts(parsed)
        assert len(txns) == 1
        assert closing == 65178.5

    def test_no_balances_available(self):
        parsed = [_txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit")]
        txns, opening, closing = _split_balance_artifacts(parsed)
        assert len(txns) == 1
        assert opening is None
        assert closing is None

    def test_newest_first_statement_by_dates(self):
        # Some banks print rows newest-first; dates reveal the direction.
        parsed = [
            _txn("NEFT CR SALARY", amount=1000.0, dc="credit", balance=66178.0, day=20),
            _txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit", balance=65178.0, day=5),
        ]
        txns, opening, closing = _split_balance_artifacts(parsed)
        assert [t.txn_date.day for t in txns] == [5, 20]
        assert opening == pytest.approx(65378.0)
        assert closing == 66178.0

    def test_newest_first_single_day_statement_by_chain(self):
        # All rows on one day: dates say nothing, the running-balance chain does.
        # Chronological truth: 65378 -200-> 65178 +1000-> 66178, printed reversed.
        parsed = [
            _txn("NEFT CR SALARY", amount=1000.0, dc="credit", balance=66178.0, day=7),
            _txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit", balance=65178.0, day=7),
        ]
        txns, opening, closing = _split_balance_artifacts(parsed)
        assert opening == pytest.approx(65378.0)
        assert closing == 66178.0


def _fake_parser(rows, bank="fake", account_ref=None):
    from src.parsers.base import StatementParser

    class FakeParser(StatementParser):
        bank_name = bank
        version = "1"

        def detect(self, path, password=None):
            return True

        def parse(self, path, password=None):
            return rows

        def extract_account_ref(self, path, password=None):
            return account_ref

    return FakeParser()


def _ingest(conn, rows, content: bytes, bank="fake", account_ref=None):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        parser = _fake_parser(rows, bank=bank, account_ref=account_ref)
        with patch("src.pipeline.ingest.detect_parser", return_value=parser):
            return ingest_pdf(path, conn=conn)
    finally:
        Path(path).unlink()


class TestIngestBalances:
    def test_artifact_row_not_persisted_and_balances_stored(self):
        conn = _make_conn()
        stmt = _ingest(conn, [
            _txn("OPENING BALANCE ...", amount=65378.0, balance=65378.0),
            _txn("UPI/ZOMATO/1/UPI", amount=200.0, dc="debit", balance=65178.0, day=5),
        ], b"%PDF balances")

        descs = [r[0] for r in conn.execute("SELECT raw_description FROM transactions").fetchall()]
        assert descs == ["UPI/ZOMATO/1/UPI"]
        row = conn.execute(
            "SELECT opening_balance, closing_balance, period_start FROM statements WHERE id=?",
            (stmt.id,),
        ).fetchone()
        assert row["opening_balance"] == 65378.0
        assert row["closing_balance"] == 65178.0
        # Artifact row must not define the statement period either.
        assert row["period_start"] == "2026-05-05"
        conn.close()

    def test_statement_of_only_artifact_rows_rejected(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="no transactions"):
            _ingest(conn, [_txn("OPENING BALANCE", amount=100.0, balance=100.0)], b"%PDF only artifact")
        conn.close()


class TestContinuityCheck:
    def _seed_statement(self, conn, month, start, end, opening, closing,
                        bank="fake", account_ref=None):
        conn.execute(
            """INSERT INTO statements (id, bank_name, parser_version, statement_month,
               period_start, period_end, opening_balance, closing_balance, account_ref)
               VALUES (?, ?, '1', ?, ?, ?, ?, ?, ?)""",
            (f"s-{bank}-{month}", bank, month, start, end, opening, closing, account_ref),
        )
        conn.commit()

    def test_matching_neighbours_produce_no_warnings(self):
        conn = _make_conn()
        self._seed_statement(conn, "2026-04", "2026-04-01", "2026-04-30", 46000.0, 90349.69)
        stmt = _ingest(conn, [
            _txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit", balance=90249.69),
        ], b"%PDF may")
        assert check_continuity(conn, stmt) == []
        conn.close()

    def test_gap_against_previous_statement_warns(self):
        conn = _make_conn()
        self._seed_statement(conn, "2026-03", "2026-03-01", "2026-03-31", 50000.0, 45994.25)
        stmt = _ingest(conn, [
            _txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit", balance=90249.69),
        ], b"%PDF may after gap")
        warnings = check_continuity(conn, stmt)
        assert len(warnings) == 1
        assert "2026-03" in warnings[0]
        assert "missing" in warnings[0]
        conn.close()

    def test_gap_against_next_statement_warns_on_out_of_order_upload(self):
        conn = _make_conn()
        self._seed_statement(conn, "2026-05", "2026-05-01", "2026-05-31", 90349.69, 90770.41)
        stmt = _ingest(conn, [
            # March statement uploaded after May; closing doesn't meet May's opening.
            Transaction(txn_date=date(2026, 3, 10), amount=100.0, debit_credit="debit",
                        raw_description="UPI/ZOMATO/1/UPI", running_balance=46094.25),
        ], b"%PDF march late")
        warnings = check_continuity(conn, stmt)
        assert len(warnings) == 1
        assert "2026-05" in warnings[0]
        conn.close()

    def test_other_bank_statements_are_not_neighbours(self):
        conn = _make_conn()
        # HDFC April closes at a completely different figure; a Kotak-May upload
        # must not be checked against it.
        self._seed_statement(conn, "2026-04", "2026-04-01", "2026-04-30",
                             10000.0, 12345.0, bank="hdfc")
        stmt = _ingest(conn, [
            _txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit", balance=90249.69),
        ], b"%PDF cross bank", bank="fake")
        assert check_continuity(conn, stmt) == []
        conn.close()

    def test_same_bank_different_account_is_not_a_neighbour(self):
        conn = _make_conn()
        self._seed_statement(conn, "2026-04", "2026-04-01", "2026-04-30",
                             10000.0, 12345.0, bank="fake", account_ref="1111222233")
        stmt = _ingest(conn, [
            _txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit", balance=90249.69),
        ], b"%PDF second account", bank="fake", account_ref="9999888877")
        assert check_continuity(conn, stmt) == []
        conn.close()

    def test_same_account_mismatch_still_warns(self):
        conn = _make_conn()
        self._seed_statement(conn, "2026-04", "2026-04-01", "2026-04-30",
                             10000.0, 12345.0, bank="fake", account_ref="1111222233")
        stmt = _ingest(conn, [
            _txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit", balance=90249.69),
        ], b"%PDF same account gap", bank="fake", account_ref="1111222233")
        warnings = check_continuity(conn, stmt)
        assert len(warnings) == 1
        assert "2026-04" in warnings[0]
        conn.close()

    def test_unknown_balances_stay_silent(self):
        conn = _make_conn()
        self._seed_statement(conn, "2026-04", "2026-04-01", "2026-04-30", None, None)
        stmt = _ingest(conn, [
            _txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit"),
        ], b"%PDF no balances")
        assert check_continuity(conn, stmt) == []
        conn.close()


class TestAccountRefExtraction:
    def _extract(self, header_text):
        from unittest.mock import MagicMock

        from src.parsers.banks.kotak import KotakParser

        page = MagicMock()
        page.extract_text.return_value = header_text
        pdf = MagicMock()
        pdf.pages = [page]
        pdf.__enter__ = lambda s: pdf
        pdf.__exit__ = lambda s, *a: False
        with patch("pdfplumber.open", return_value=pdf):
            return KotakParser().extract_account_ref("whatever.pdf")

    @pytest.mark.parametrize("text,expected", [
        ("Kotak Mahindra Bank\nAccount No. 3250508074\nStatement", "3250508074"),
        ("A/c Number: XXXX-1234", "XXXX-1234"),
        ("account # 1234567890 savings", "1234567890"),
        ("Statement of account for May", None),  # no number present
        ("", None),
    ])
    def test_header_patterns(self, text, expected):
        assert self._extract(text) == expected

    def test_unreadable_pdf_returns_none(self):
        from src.parsers.banks.kotak import KotakParser

        assert KotakParser().extract_account_ref("/nonexistent/file.pdf") is None


class TestUploadRouteWarnings:
    def test_upload_response_includes_continuity_warnings(self):
        from src.api.deps import get_db as api_get_db
        from src.main import app

        conn = _make_conn()
        conn.execute(
            """INSERT INTO statements (id, bank_name, parser_version, statement_month,
               period_start, period_end, opening_balance, closing_balance)
               VALUES ('s-apr', 'fake', '1', '2026-04', '2026-04-01', '2026-04-30', 46000.0, 12345.0)"""
        )
        conn.commit()
        app.dependency_overrides[api_get_db] = lambda: conn
        try:
            from fastapi.testclient import TestClient

            client = TestClient(app)
            rows = [_txn("UPI/ZOMATO/1/UPI", amount=100.0, dc="debit", balance=90249.69)]
            with patch("src.pipeline.ingest.detect_parser", return_value=_fake_parser(rows)):
                resp = client.post(
                    "/api/statements/upload",
                    files={"file": ("stmt.pdf", b"%PDF warn test", "application/pdf")},
                )
            assert resp.status_code == 200
            body = resp.json()
            assert body["opening_balance"] == pytest.approx(90349.69)
            assert len(body["warnings"]) == 1
            assert "2026-04" in body["warnings"][0]
        finally:
            app.dependency_overrides.pop(api_get_db, None)
            conn.close()
