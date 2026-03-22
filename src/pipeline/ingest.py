"""PDF → parsed transactions: invoke parser, persist statements and transactions."""
from __future__ import annotations

import sqlite3

from src.db.connection import get_db
from src.db.queries.transactions import insert_statement, insert_transaction
from src.models.transaction import Statement
from src.parsers.registry import detect_parser


def ingest_pdf(
    pdf_path: str,
    password: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> Statement:
    """Parse a PDF and persist the statement + all transactions to the DB.

    Returns the created Statement. Raises ValueError if no parser recognises the PDF.
    """
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
    )

    for txn in transactions:
        txn.statement_id = statement.id

    _conn = conn or get_db()
    try:
        insert_statement(_conn, statement)
        for txn in transactions:
            insert_transaction(_conn, txn)
        _conn.commit()
    finally:
        if conn is None:
            _conn.close()

    return statement
