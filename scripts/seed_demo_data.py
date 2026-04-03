"""Seed the DB with realistic synthetic data for demo screenshots and walkthroughs.

All data is entirely fictional — no real bank accounts, people, or transactions.

Usage:
    uv run python scripts/seed_demo_data.py
    uv run python scripts/seed_demo_data.py --wipe   # clear demo data first
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.connection import get_db

STATEMENT_ID = "demo_stmt_kotak_mar26"
STATEMENT_MONTH = "2026-03"

# fmt: off
# (txn_id, date, amount, debit_credit, raw_description, upi_note)
# Running balances are computed automatically from OPENING_BALANCE.
OPENING_BALANCE = 152480.75

_RAW_TRANSACTIONS = [
    # --- Salary & income ---
    ("demo_t01", "2026-03-01", 125000.00, "credit", "NEFT/SAL CREDIT/TECHWAVE SOLUTIONS PVT LTD",      "Salary March 2026"),

    # --- Rent & housing ---
    ("demo_t02", "2026-03-01", 22000.00, "debit",  "UPI/AMIT.LANDLORD@OKSBI",                          "March rent"),
    ("demo_t03", "2026-03-02", 3500.00,  "debit",  "NEFT/PRESTIGE PALM OWNERS ASSN",                   ""),

    # --- Food delivery (should match rules) ---
    ("demo_t04", "2026-03-02", 389.00,   "debit",  "UPI/SWIGGY/ORDER/9182736450",                      "Swiggy dinner biryani"),
    ("demo_t05", "2026-03-04", 542.00,   "debit",  "UPI/ZOMATO/PAYMENT",                               "Zomato lunch order"),
    ("demo_t06", "2026-03-08", 267.00,   "debit",  "UPI/SWIGGY/ORDER/9182736498",                      "Swiggy breakfast dosa"),
    ("demo_t07", "2026-03-15", 478.00,   "debit",  "UPI/ZOMATO/PAYMENT",                               "Pizza night Zomato"),
    ("demo_t08", "2026-03-22", 315.00,   "debit",  "UPI/SWIGGY/ORDER/9182736521",                      "Swiggy thali order"),

    # --- Groceries ---
    ("demo_t09", "2026-03-03", 1850.00,  "debit",  "UPI/BIGBASKET/GROCERIES",                          "BigBasket weekly groceries"),
    ("demo_t10", "2026-03-10", 2100.00,  "debit",  "UPI/BIGBASKET/GROCERIES",                          "BigBasket monthly stock"),
    ("demo_t11", "2026-03-17", 430.00,   "debit",  "UPI/ZEPTO/QUICK DELIVERY",                         "Zepto milk eggs bread"),

    # --- Transport (rules) ---
    ("demo_t12", "2026-03-03", 185.00,   "debit",  "UPI/UBER/RIDE/BANGALORE",                          "Uber to HSR Layout"),
    ("demo_t13", "2026-03-06", 220.00,   "debit",  "UPI/OLA/CAB/BLR",                                  "Ola to Whitefield"),
    ("demo_t14", "2026-03-14", 340.00,   "debit",  "UPI/UBER/RIDE/BANGALORE",                          "Uber airport drop"),
    ("demo_t15", "2026-03-20", 150.00,   "debit",  "UPI/RAPIDO/BIKE",                                  "Rapido to Indiranagar"),

    # --- Bills & subscriptions (rules) ---
    ("demo_t16", "2026-03-05", 499.00,   "debit",  "UPI/JIO/RECHARGE",                                 "Jio monthly plan"),
    ("demo_t17", "2026-03-05", 249.00,   "debit",  "NETFLIX SUBSCRIPTION AUTO DEBIT",                  ""),
    ("demo_t18", "2026-03-05", 119.00,   "debit",  "SPOTIFY PREMIUM SUBSCRIPTION",                     ""),
    ("demo_t19", "2026-03-07", 1249.00,  "debit",  "ACT FIBERNET BROADBAND PAYMENT",                   ""),
    ("demo_t20", "2026-03-10", 1850.00,  "debit",  "BESCOM ELECTRICITY BILL MARCH",                    ""),

    # --- Shopping (rules) ---
    ("demo_t21", "2026-03-09", 3499.00,  "debit",  "AMAZON PAYMENTS INDIA PVT LTD",                    "Amazon headphones"),
    ("demo_t22", "2026-03-12", 1899.00,  "debit",  "FLIPKART INTERNET PVT LTD",                        "Flipkart backpack"),
    ("demo_t23", "2026-03-25", 749.00,   "debit",  "MYNTRA DESIGNS PVT LTD",                           "Myntra t-shirts"),

    # --- Health ---
    ("demo_t24", "2026-03-11", 580.00,   "debit",  "UPI/PHARMEASY/MEDICINES",                          "Medicine order monthly"),
    ("demo_t25", "2026-03-18", 1200.00,  "debit",  "UPI/PRACTO/DOCTOR CONSULT",                        "Doctor consultation"),

    # --- Finance (EMIs, investments) ---
    ("demo_t26", "2026-03-05", 15000.00, "debit",  "NEFT/EMI/HDFC HOME LOAN ACCOUNT",                  "Home loan EMI"),
    ("demo_t27", "2026-03-10", 5000.00,  "debit",  "UPI/GROWW/SIP/NIPPON INDIA SMALL CAP",             "SIP March"),
    ("demo_t28", "2026-03-10", 5000.00,  "debit",  "UPI/ZERODHA/COIN/SIP",                             "PPFAS Flexi Cap SIP"),

    # --- Travel ---
    ("demo_t29", "2026-03-13", 2450.00,  "debit",  "IRCTC TICKET BOOKING",                             "Train BLR to Chennai"),
    ("demo_t30", "2026-03-14", 4800.00,  "debit",  "MAKEMYTRIP/HOTEL BOOKING",                         "Hotel Chennai 2 nights"),

    # --- Peer transfers (ambiguous — should test LLM / known-person matching) ---
    ("demo_t31", "2026-03-06", 2500.00,  "debit",  "UPI/PRIYA.SHARMA92@OKICICI",                       "birthday gift"),
    ("demo_t32", "2026-03-09", 750.00,   "debit",  "UPI/RAHUL.MEHTA@YBL",                              "lunch split last week"),
    ("demo_t33", "2026-03-16", 1500.00,  "debit",  "UPI/NEHA.GUPTA@PAYTM",                             "movie tickets share"),
    ("demo_t34", "2026-03-21", 5000.00,  "debit",  "IMPS/P2P/TRANSFER/TO SAVINGS",                     ""),
    ("demo_t35", "2026-03-28", 300.00,   "debit",  "UPI/CHAI.WALA.CORNER@PAYTM",                       "chai and samosa"),

    # --- Ambiguous / edge cases (should fall through to LLM, some low-confidence) ---
    ("demo_t36", "2026-03-19", 1200.00,  "debit",  "UPI/9988776655@OKAXIS",                            "annual thing"),
    ("demo_t37", "2026-03-23", 650.00,   "debit",  "NEFT/REF789012/CLEARVIEW SERVICES LTD",            ""),
    ("demo_t38", "2026-03-24", 2000.00,  "debit",  "UPI/DECATHLON.BLR@HDFCBANK",                       "running shoes"),
    ("demo_t39", "2026-03-26", 350.00,   "debit",  "POS/THIRD WAVE COFFEE ROASTERS HSR",               ""),
    ("demo_t40", "2026-03-27", 890.00,   "debit",  "UPI/LENSKART/ORDER",                               "New glasses power change"),

    # --- More credits ---
    ("demo_t41", "2026-03-08", 200.00,   "credit", "UPI/REFUND/SWIGGY",                                "Swiggy refund cancelled"),
    ("demo_t42", "2026-03-15", 1500.00,  "credit", "UPI/RAHUL.MEHTA@YBL",                              "settled up dinner"),
    ("demo_t43", "2026-03-20", 150.00,   "credit", "CASHBACK/CRED REWARD",                             ""),

    # --- Personal care / fitness ---
    ("demo_t44", "2026-03-12", 1500.00,  "debit",  "UPI/CULT.FIT/MEMBERSHIP",                          "Cult.fit monthly"),
    ("demo_t45", "2026-03-22", 700.00,   "debit",  "UPI/NATURALS.SALON@YBL",                           "Haircut and grooming"),

    # --- ATM ---
    ("demo_t46", "2026-03-11", 5000.00,  "debit",  "ATM WITHDRAWAL SBI KORAMANGALA",                   ""),
    ("demo_t47", "2026-03-25", 2000.00,  "debit",  "ATM WITHDRAWAL HDFC INDIRANAGAR",                  ""),

    # --- Education ---
    ("demo_t48", "2026-03-15", 999.00,   "debit",  "UDEMY COURSE PURCHASE",                            ""),

    # --- Gifts & donations ---
    ("demo_t49", "2026-03-26", 1100.00,  "debit",  "UPI/KETTO/DONATION",                               "Medical fundraiser"),

    # --- Credit card payment ---
    ("demo_t50", "2026-03-28", 18500.00, "debit",  "NEFT/HDFC CREDIT CARD PAYMENT",                    ""),
]

# Sort by date (stable — preserves insertion order within same day) and compute running balances.
_sorted = sorted(_RAW_TRANSACTIONS, key=lambda t: t[1])
_balance = OPENING_BALANCE
TRANSACTIONS: list[tuple] = []
for _txn_id, _date, _amount, _dc, _desc, _note in _sorted:
    if _dc == "credit":
        _balance += _amount
    else:
        _balance -= _amount
    TRANSACTIONS.append((_txn_id, _date, _amount, _dc, _desc, _note, round(_balance, 2)))
# fmt: on

# Pre-built annotations for some transactions so the dashboard looks populated.
# These simulate a mix of rule-matched, rag_direct, rag_prompted, llm, and manual annotations.
# Transactions not listed here remain unannotated → ready for the auto-annotate demo.
#
# Confidence scores are spread realistically:
#   rule: fixed 0.95
#   rag_direct: 0.78–0.88 (cosine × agreement × margin — often dips below threshold)
#   rag_prompted: 0.62–0.79 (llm_conf × dampening ~0.92 — frequently in review queue)
#   llm: 0.32–0.65 (llm_conf × dampening ~0.85 — most land in review queue)
#   manual: 1.0
#
# (txn_id, merchant, category, subcategory, tags, confidence, source)
ANNOTATIONS = [
    # Rule matches — only clear merchant keywords get 0.95
    ("demo_t04", "Swiggy",              "Food & Dining",     "Food Delivery",     "food,delivery",       0.95,   "rule"),
    ("demo_t05", "Zomato",              "Food & Dining",     "Food Delivery",     "food,delivery",       0.95,   "rule"),
    ("demo_t09", "BigBasket",           "Food & Dining",     "Groceries",         "groceries",           0.95,   "rule"),
    ("demo_t12", "Uber",                "Transport",         "Cab & Auto",        "cab,commute",         0.95,   "rule"),
    ("demo_t16", "Jio",                 "Bills & Utilities", "Mobile Recharge",   "mobile,recharge",     0.95,   "rule"),
    ("demo_t17", "Netflix",             "Entertainment",     "Movies & OTT",      "subscription,ott",    0.95,   "rule"),
    ("demo_t21", "Amazon",              "Shopping",          "Online Shopping",    "online,shopping",     0.95,   "rule"),
    ("demo_t26", "HDFC Home Loan",      "Finances",          "Loan EMI",          "emi,home-loan",       0.95,   "rule"),

    # RAG direct — similarity is high but agreement/margin factors pull confidence down
    ("demo_t06", "Swiggy",              "Food & Dining",     "Food Delivery",     "food,delivery",       0.88,   "rag_direct"),
    ("demo_t10", "BigBasket",           "Food & Dining",     "Groceries",         "groceries",           0.84,   "rag_direct"),
    ("demo_t13", "Ola",                 "Transport",         "Cab & Auto",        "cab",                 0.82,   "rag_direct"),
    ("demo_t14", "Uber",                "Transport",         "Cab & Auto",        "cab,airport",         0.78,   "rag_direct"),

    # RAG prompted — LLM with examples, dampened; many below 0.85 threshold
    ("demo_t07", "Zomato",              "Food & Dining",     "Food Delivery",     "food,delivery",       0.79,   "rag_prompted"),
    ("demo_t08", "Swiggy",              "Food & Dining",     "Food Delivery",     "food,delivery",       0.74,   "rag_prompted"),
    ("demo_t11", "Zepto",               "Food & Dining",     "Groceries",         "groceries,quick",     0.71,   "rag_prompted"),
    ("demo_t22", "Flipkart",            "Shopping",          "Online Shopping",    "online,shopping",     0.76,   "rag_prompted"),
    ("demo_t30", "MakeMyTrip",          "Travel",            "Hotels",            "hotel,travel",        0.62,   "rag_prompted"),
    ("demo_t44", "Cult.fit",            "Personal Care",     "Gym & Fitness",     "fitness,membership",  0.68,   "rag_prompted"),

    # LLM (cold) — no examples, heavily dampened; most are well below threshold
    ("demo_t31", "Priya Sharma",        "Gifts & Donations", "Personal Gifts",   "gift,birthday",       0.58,   "llm"),
    ("demo_t32", "Rahul Mehta",         "Food & Dining",     "Restaurants",       "split,lunch",         0.52,   "llm"),
    ("demo_t36", None,                  "Uncategorized",     None,                "",                    0.32,   "llm"),
    ("demo_t37", "Clearview Services",  "Bills & Utilities", None,                "",                    0.41,   "llm"),
    ("demo_t35", None,                  "Food & Dining",     "Cafe & Snacks",     "chai",                0.48,   "llm"),
    ("demo_t38", "Decathlon",           "Shopping",          "General Retail",    "sports",              0.55,   "llm"),
    ("demo_t39", "Third Wave Coffee",   "Food & Dining",     "Cafe & Snacks",     "coffee",              0.65,   "llm"),
    ("demo_t40", "Lenskart",            "Health",            "Pharmacy",          "eyewear",             0.47,   "llm"),

    # Manual corrections (user reviewed and fixed)
    ("demo_t01", "TechWave Solutions",  "Income",            "Salary",            "salary,monthly",      1.0,    "manual"),
    ("demo_t02", "Amit (Landlord)",     "Housing",           "Rent",              "rent,monthly",        1.0,    "manual"),
    ("demo_t34", None,                  "Transfers",         "Self Transfer",     "savings",             1.0,    "manual"),
    ("demo_t46", "SBI ATM",             "Transfers",         "ATM Withdrawal",    "atm,cash",            1.0,    "manual"),
]

# Synthetic people for known-person matching demo
# (id, name, upi)
PEOPLE = [
    ("demo_p01", "Priya Sharma",  "priya.sharma92@okicici"),
    ("demo_p02", "Rahul Mehta",   "rahul.mehta@ybl"),
    ("demo_p03", "Neha Gupta",    "neha.gupta@paytm"),
    ("demo_p04", "Amit Kumar",    "amit.landlord@oksbi"),
]


def wipe(conn) -> None:
    conn.execute(
        "DELETE FROM annotations WHERE transaction_id IN "
        "(SELECT id FROM transactions WHERE statement_id = ?)",
        (STATEMENT_ID,),
    )
    conn.execute("DELETE FROM transactions WHERE statement_id = ?", (STATEMENT_ID,))
    conn.execute("DELETE FROM statements WHERE id = ?", (STATEMENT_ID,))
    conn.execute("DELETE FROM people WHERE id LIKE 'demo_p%'")
    conn.commit()
    print("Wiped existing demo data.")


def seed(conn) -> None:
    # Statement
    conn.execute(
        "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, ?, ?, ?)",
        (STATEMENT_ID, "kotak", "1", STATEMENT_MONTH),
    )

    # Transactions
    txn_inserted = 0
    txn_skipped = 0
    for txn_id, date, amount, dc, desc, note, balance in TRANSACTIONS:
        existing = conn.execute("SELECT id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if existing:
            txn_skipped += 1
            continue
        upi_meta = json.dumps({"note": note}) if note else None
        conn.execute(
            """INSERT INTO transactions
               (id, statement_id, txn_date, amount, debit_credit, raw_description, running_balance, upi_meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (txn_id, STATEMENT_ID, date, amount, dc, desc, balance, upi_meta),
        )
        txn_inserted += 1

    # Annotations
    ann_inserted = 0
    for txn_id, merchant, category, subcategory, tags, confidence, source in ANNOTATIONS:
        existing = conn.execute(
            "SELECT id FROM annotations WHERE transaction_id = ?", (txn_id,)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """INSERT INTO annotations (id, transaction_id, merchant, category, subcategory, tags, confidence, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"ann_{txn_id}", txn_id, merchant, category, subcategory, tags, confidence, source),
        )
        ann_inserted += 1

    # People
    ppl_inserted = 0
    for pid, name, upi in PEOPLE:
        existing = conn.execute("SELECT id FROM people WHERE id = ?", (pid,)).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO people (id, name, upi) VALUES (?, ?, ?)",
            (pid, name, upi),
        )
        ppl_inserted += 1

    conn.commit()

    # Count unannotated for the user
    unannotated = conn.execute(
        """SELECT COUNT(*) FROM transactions t
           LEFT JOIN annotations a ON t.id = a.transaction_id
           WHERE t.statement_id = ? AND a.id IS NULL""",
        (STATEMENT_ID,),
    ).fetchone()[0]

    low_conf = conn.execute(
        """SELECT COUNT(*) FROM annotations
           WHERE transaction_id LIKE 'demo_%' AND confidence < 0.85""",
    ).fetchone()[0]

    print(f"Seeded {txn_inserted} transactions ({txn_skipped} already existed)")
    print(f"Seeded {ann_inserted} annotations")
    print(f"Seeded {ppl_inserted} people")
    print()
    print(f"  {unannotated} transactions left unannotated (ready for auto-annotate)")
    print(f"  {low_conf} annotations below confidence threshold (will appear in review queue)")
    print()
    print("Run auto-annotate on remaining transactions:")
    print(f'  curl -X POST http://localhost:8000/annotations/auto-annotate \\')
    print(f'       -H "Content-Type: application/json" \\')
    print(f'       -d \'{{"statement_id": "{STATEMENT_ID}"}}\' | python3 -m json.tool')


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data for screenshots and walkthroughs")
    parser.add_argument("--wipe", action="store_true", help="Delete existing demo data")
    args = parser.parse_args()

    conn = get_db()
    if args.wipe:
        wipe(conn)
    else:
        seed(conn)
    conn.close()


if __name__ == "__main__":
    main()
