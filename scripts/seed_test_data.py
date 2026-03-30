"""Seed the DB with synthetic transactions for testing the auto-annotate pipeline.

Usage:
    uv run python scripts/seed_test_data.py
    uv run python scripts/seed_test_data.py --wipe   # clear existing seed data first
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.connection import get_db

STATEMENT_ID = "seed_stmt_test_01"
STATEMENT_MONTH = "2026-01"

# (txn_id, date, amount, debit_credit, raw_description, upi_note)
TRANSACTIONS = [
    # --- Should match rules ---
    ("seed_t01", "2026-01-02", 349.00,  "debit",  "UPI/SWIGGY/ORDER/1234567890",        "Swiggy food order"),
    ("seed_t02", "2026-01-03", 680.00,  "debit",  "UPI/ZOMATO/PAYMENT",                 "Dinner Zomato"),
    ("seed_t03", "2026-01-04", 1200.00, "debit",  "UPI/BIGBASKET/GROCERIES",             "BigBasket weekly"),
    ("seed_t04", "2026-01-05", 249.00,  "debit",  "NETFLIX SUBSCRIPTION AUTO DEBIT",    ""),
    ("seed_t05", "2026-01-06", 150.00,  "debit",  "UPI/UBER/RIDE/BANGALORE",             "Uber to office"),
    ("seed_t06", "2026-01-07", 500.00,  "debit",  "UPI/JIO/RECHARGE",                   "Jio monthly plan"),
    ("seed_t07", "2026-01-08", 2499.00, "debit",  "AMAZON PAYMENTS INDIA PVT LTD",      "Amazon order"),
    ("seed_t08", "2026-01-09", 799.00,  "debit",  "IRCTC TICKET BOOKING",               "Train Bangalore Chennai"),
    ("seed_t09", "2026-01-10", 650.00,  "debit",  "UPI/OLA/CAB/BLR",                    "Ola cab airport"),
    ("seed_t10", "2026-01-11", 100.00,  "debit",  "ATM WITHDRAWAL SBI KORAMANGALA",     ""),
    ("seed_t11", "2026-01-12", 199.00,  "debit",  "SPOTIFY PREMIUM SUBSCRIPTION",       ""),
    ("seed_t12", "2026-01-13", 3500.00, "debit",  "NEFT/EMI/HDFC HOME LOAN",            "Home loan EMI"),
    ("seed_t13", "2026-01-14", 450.00,  "debit",  "UPI/PHARMEASY/MEDICINES",            "Medicine order"),
    ("seed_t14", "2026-01-15", 899.00,  "debit",  "FLIPKART INTERNET PVT LTD",          "Flipkart purchase"),
    ("seed_t15", "2026-01-16", 1500.00, "debit",  "UPI/CULT.FIT/MEMBERSHIP",            "Gym membership"),

    # --- Ambiguous — should fall through to LLM ---
    ("seed_t16", "2026-01-17", 2000.00, "debit",  "UPI/9876543210@okaxis",              "birthday gift bro"),
    ("seed_t17", "2026-01-18", 450.00,  "debit",  "UPI/rajesh.kumar99@ybl",             "lunch split"),
    ("seed_t18", "2026-01-19", 800.00,  "debit",  "NEFT/REF123456/ACME SERVICES LTD",  ""),
    ("seed_t19", "2026-01-20", 300.00,  "debit",  "UPI/shopkeeper@paytm",               "vegetables"),
    ("seed_t20", "2026-01-21", 5000.00, "debit",  "IMPS/P2P/TRANSFER/SAVINGS",          ""),

    # --- Credits (income) ---
    ("seed_t21", "2026-01-01", 85000.00, "credit", "NEFT/SAL CREDIT/ACME CORP LTD",    "Salary January"),
    ("seed_t22", "2026-01-15", 200.00,   "credit", "UPI/REFUND/SWIGGY",                "Swiggy refund"),
    ("seed_t23", "2026-01-22", 1000.00,  "credit", "UPI/9876543210@okaxis",            "money from mom"),
]


def wipe(conn) -> None:
    conn.execute(
        "DELETE FROM annotations WHERE transaction_id IN "
        "(SELECT id FROM transactions WHERE statement_id = ?)",
        (STATEMENT_ID,),
    )
    conn.execute("DELETE FROM transactions WHERE statement_id = ?", (STATEMENT_ID,))
    conn.execute("DELETE FROM statements WHERE id = ?", (STATEMENT_ID,))
    conn.commit()
    print("Wiped existing seed data.")


def seed(conn) -> None:
    # Insert statement
    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, ?, ?, ?)",
        (STATEMENT_ID, "seed/test", "0", STATEMENT_MONTH),
    )

    inserted = 0
    skipped = 0
    for txn_id, date, amount, dc, desc, note in TRANSACTIONS:
        existing = conn.execute("SELECT id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if existing:
            skipped += 1
            continue
        upi_meta = json.dumps({"note": note}) if note else None
        conn.execute(
            """INSERT INTO transactions
               (id, statement_id, txn_date, amount, debit_credit, raw_description, upi_meta)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (txn_id, STATEMENT_ID, date, amount, dc, desc, upi_meta),
        )
        inserted += 1

    conn.commit()
    print(f"Seeded {inserted} transactions ({skipped} already existed).")
    print(f"statement_id: {STATEMENT_ID}")
    print()
    print("Trigger annotation:")
    print(f'  curl -X POST http://localhost:8000/annotations/auto-annotate \\')
    print(f'       -H "Content-Type: application/json" \\')
    print(f'       -d \'{{"statement_id": "{STATEMENT_ID}"}}\' | python3 -m json.tool')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wipe", action="store_true", help="Delete existing seed data before inserting")
    args = parser.parse_args()

    conn = get_db()
    if args.wipe:
        wipe(conn)
        conn.close()
        return
    seed(conn)
    conn.close()


if __name__ == "__main__":
    main()
