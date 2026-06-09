"""PDF → parsed transactions: invoke parser, persist statements and transactions."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from src.db.connection import get_db
from src.db.queries.transactions import insert_statement, insert_transaction
from src.models.transaction import Statement
from src.parsers.registry import detect_parser


class DuplicateStatementError(ValueError):
    """Raised when the exact same PDF (by content hash) was already uploaded."""

    def __init__(self, existing: dict):
        super().__init__(
            f"This statement was already uploaded ({existing['bank_name']}, "
            f"{existing['statement_month']})"
        )
        self.existing = existing


def ingest_pdf(
    pdf_path: str,
    password: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> Statement:
    """Parse a PDF and persist the statement + all transactions to the DB.

    Returns the created Statement. Raises ValueError if no parser recognises the
    PDF, or DuplicateStatementError if this exact file was already ingested.
    """
    file_sha256 = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()

    _conn = conn or get_db()
    try:
        existing = _conn.execute(
            "SELECT * FROM statements WHERE file_sha256 = ?", (file_sha256,)
        ).fetchone()
        if existing:
            raise DuplicateStatementError(dict(existing))

        parser = detect_parser(pdf_path, password=password)
        if parser is None:
            raise ValueError(f"No parser found for PDF: {pdf_path}")

        transactions = parser.parse(pdf_path, password=password)
        if not transactions:
            raise ValueError(f"Parser returned no transactions for: {pdf_path}")

        statement_month = transactions[0].txn_date.strftime("%Y-%m")
        statement = Statement(
            bank_name=parser.bank_name,
            parser_version=parser.version,
            statement_month=statement_month,
            file_sha256=file_sha256,
        )

        for txn in transactions:
            txn.statement_id = statement.id

        insert_statement(_conn, statement)
        for txn in transactions:
            insert_transaction(_conn, txn)
        _conn.commit()
    finally:
        if conn is None:
            _conn.close()

    return statement
