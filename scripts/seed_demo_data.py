"""Seed a self-contained demo ledger with realistic synthetic data.

All data is entirely fictional - no real bank accounts, people, or transactions.

The generator is anchored to today's date and produces three full months of
history plus the current partial month, so the demo always looks current:

- Recurring commitments (rent, EMI, SIPs, subscriptions) repeat monthly with
  stable amounts and UPI counterparties, so the Money Map recurring panel,
  learned rules, and the counterparty prior all light up.
- History months are fully annotated with a realistic mix of pipeline sources
  and confidences; the current month carries a small low-confidence review
  backlog plus a batch of unannotated transactions for a live auto-annotate.
- Story arcs exercise the insights netting machinery: a trip expense group
  whose friends paid their shares back (group split offsets), a linked
  merchant refund (transaction_links offset), an unlinked refund matched by
  counterparty, and a monthly self-transfer excluded from the cash verdict.

By default this seeds data/demo.db (never the real ledger). Serve it with:

    uv run python scripts/seed_demo_data.py
    DB_PATH=data/demo.db PORT=8080 uv run python -m src

Other usage:

    uv run python scripts/seed_demo_data.py --wipe          # remove demo rows
    uv run python scripts/seed_demo_data.py --db path.db    # custom target
"""
from __future__ import annotations

import argparse
import calendar
import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.connection import get_migrated_db
from src.pipeline.counterparty import normalize_identity

DEFAULT_DB = str(Path(__file__).parent.parent / "data" / "demo.db")

OPENING_BALANCE = 152480.75

# (id, name, upi, relationship)
PEOPLE = [
    ("demo_p01", "Priya Sharma", "priya.sharma92@okicici", "friend"),
    ("demo_p02", "Rahul Mehta", "rahul.mehta@ybl", "friend"),
    ("demo_p03", "Neha Gupta", "neha.gupta@paytm", "friend"),
    ("demo_p04", "Amit Kumar", "amit.landlord@oksbi", "landlord"),
]


def _month_add(anchor: date, offset: int) -> tuple[int, int]:
    idx = anchor.year * 12 + (anchor.month - 1) + offset
    return idx // 12, idx % 12 + 1


def _clamp_date(year: int, month: int, day: int) -> str:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last)).isoformat()


class Ledger:
    """Accumulates transactions/annotations and assigns stable per-month ids."""

    def __init__(self, today: date):
        self.today = today
        self.txns: list[dict] = []       # raw rows, running balance filled later
        self.groups: list[dict] = []
        self.links: list[tuple[str, str, str, str]] = []  # (a, b, type, note)
        self._seq: dict[str, int] = {}

    def add(
        self,
        year: int,
        month: int,
        day: int,
        amount: float,
        dc: str,
        desc: str,
        note: str = "",
        ann: tuple | None = None,
    ) -> str | None:
        """Add one transaction; returns its id, or None if the date is in the
        future (current-month items that have not happened yet are dropped)."""
        txn_date = _clamp_date(year, month, day)
        if txn_date > self.today.isoformat():
            return None
        ym = f"{year}-{month:02d}"
        self._seq[ym] = self._seq.get(ym, 0) + 1
        txn_id = f"demo_{year}{month:02d}_{self._seq[ym]:03d}"
        self.txns.append({
            "id": txn_id,
            "statement_id": f"demo_stmt_{year}_{month:02d}",
            "month": ym,
            "date": txn_date,
            "amount": round(amount, 2),
            "dc": dc,
            "desc": desc,
            "note": note,
            "counterparty_key": normalize_identity(desc),
            # (merchant, category, subcategory, tags, source, confidence)
            "ann": ann,
        })
        return txn_id


def _fixed_monthly(led: Ledger, year: int, month: int, rng: random.Random, first_month: bool) -> None:
    """Commitments that repeat every month with stable amounts and dates."""
    # Salary and rent land on the 1st; salary is inserted first so the balance
    # never dips at the top of the month.
    led.add(year, month, 1, 125000, "credit",
            "NEFT/SAL CREDIT/TECHWAVE SOLUTIONS PVT LTD",
            f"Salary {calendar.month_name[month]}",
            ("TechWave Solutions", "Income", "Salary", "salary,monthly", "manual" if first_month else "learned_rule", 1.0 if first_month else 0.97))
    led.add(year, month, 1, 22000, "debit", "UPI/AMIT.LANDLORD@OKSBI",
            f"{calendar.month_name[month]} rent",
            ("Amit Kumar", "Housing", "Rent", "rent,monthly", "manual", 1.0))
    led.add(year, month, 2, 3500, "debit", "NEFT/PRESTIGE PALM OWNERS ASSN", "",
            ("Prestige Palm Society", "Housing", "Maintenance & Society Charges", "maintenance", "manual" if first_month else "learned_rule", 1.0 if first_month else 0.96))

    # SIPs on the 3rd so even an early-month demo shows Invested > 0.
    led.add(year, month, 3, 5000, "debit", "UPI/GROWW/SIP/NIPPON INDIA SMALL CAP", "Monthly SIP",
            ("Groww", "Investments", "Mutual Fund SIP", "sip,investment", "rule", 0.95))
    led.add(year, month, 3, 5000, "debit", "UPI/ZERODHA COIN/SIP/PPFAS FLEXI CAP", "Monthly SIP",
            ("Zerodha Coin", "Investments", "Mutual Fund SIP", "sip,investment", "rule", 0.95))

    # Subscriptions and the home loan EMI on the 5th.
    led.add(year, month, 5, 349, "debit", "UPI/JIO/AUTOPAY", "Jio monthly plan",
            ("Jio", "Bills & Utilities", "Mobile Recharge", "mobile,recharge", "rule", 0.95))
    led.add(year, month, 5, 649, "debit", "UPI/NETFLIX/AUTOPAY", "",
            ("Netflix", "Entertainment", "Movies & OTT", "subscription,ott", "rule", 0.95))
    led.add(year, month, 5, 119, "debit", "UPI/SPOTIFY/AUTOPAY", "",
            ("Spotify", "Entertainment", "Movies & OTT", "subscription,music", "rule", 0.95))
    led.add(year, month, 5, 15000, "debit", "NEFT/EMI/HDFC HOME LOAN ACCOUNT", "Home loan EMI",
            ("HDFC Home Loan", "Finances", "Loan EMI", "emi,home-loan", "rule", 0.95))
    led.add(year, month, 7, 1249, "debit", "UPI/ACT FIBERNET/BROADBAND", "",
            ("ACT Fibernet", "Bills & Utilities", "Internet & Broadband", "internet,bill", "learned_rule", 0.94))

    # Electricity varies month to month (it is a bill, not a fixed commitment).
    led.add(year, month, 10, rng.choice([1420, 1730, 1980, 2260]), "debit",
            "UPI/BESCOM/BBPS BILL PAYMENT", "Electricity bill",
            ("BESCOM", "Bills & Utilities", "Electricity", "electricity,bill", "rag_direct", round(rng.uniform(0.88, 0.93), 2)))

    led.add(year, month, 12, 1500, "debit", "UPI/CULT.FIT/MEMBERSHIP", "Cult.fit monthly",
            ("Cult.fit", "Personal Care", "Gym & Fitness", "fitness,membership", "learned_rule", 0.95))

    # Cashback trickle, credit card bill, and the monthly self-transfer.
    led.add(year, month, 20, rng.choice([104, 151, 187]), "credit", "CASHBACK/CRED REWARD", "",
            ("CRED", "Income", "Cashback", "cashback", "rag_prompted", round(rng.uniform(0.80, 0.88), 2)))
    led.add(year, month, 25, rng.choice([13260, 15840, 17410]), "debit", "NEFT/HDFC CREDIT CARD PAYMENT", "",
            ("HDFC Credit Card", "Finances", "Credit Card Payment", "credit-card", "learned_rule", 0.96))
    led.add(year, month, 28, 10000, "debit", "IMPS/P2P/TRANSFER/TO OWN SBI SAVINGS", "",
            (None, "Self Transfers", "Self Transfer", "savings", "manual", 1.0))


def _variable_monthly(led: Ledger, year: int, month: int, rng: random.Random) -> None:
    """Discretionary spending with realistic jitter in counts and amounts."""
    def scatter(count: int, lo_day: int, hi_day: int) -> list[int]:
        return sorted(rng.sample(range(lo_day, hi_day + 1), count))

    for day in scatter(rng.randint(5, 7), 2, 28):
        led.add(year, month, day, rng.randint(180, 640), "debit",
                f"UPI/SWIGGY/ORDER/{rng.randint(9100000000, 9199999999)}",
                rng.choice(["Swiggy dinner", "Swiggy biryani", "Swiggy thali", "Swiggy breakfast dosa"]),
                ("Swiggy", "Food & Dining", "Food Delivery", "food,delivery", "rule", 0.95))
    for day in scatter(rng.randint(2, 4), 3, 27):
        led.add(year, month, day, rng.randint(220, 720), "debit", "UPI/ZOMATO/PAYMENT",
                rng.choice(["Zomato lunch order", "Pizza night", "Weekend momos"]),
                ("Zomato", "Food & Dining", "Food Delivery", "food,delivery", "rule", 0.95))

    for day in scatter(rng.randint(3, 4), 2, 27):
        led.add(year, month, day, rng.randint(1450, 2450), "debit", "UPI/BIGBASKET/GROCERIES",
                "Weekly groceries",
                ("BigBasket", "Food & Dining", "Groceries", "groceries", "rule", 0.95))
    for day in scatter(rng.randint(2, 3), 4, 26):
        led.add(year, month, day, rng.randint(240, 580), "debit", "UPI/ZEPTO MARKETPLA/ORDER",
                "Zepto milk eggs bread",
                ("Zepto", "Food & Dining", "Groceries", "groceries,quick", "learned_rule", 0.93))

    for day in scatter(rng.randint(4, 6), 2, 28):
        provider = rng.choice([
            ("UPI/UBER/RIDE/BANGALORE", "Uber", "rule", 0.95),
            ("UPI/OLA/CAB/BLR", "Ola", "rule", 0.95),
            ("UPI/RAPIDO/BIKE", "Rapido", "learned_rule", 0.92),
        ])
        led.add(year, month, day, rng.randint(90, 420), "debit", provider[0],
                rng.choice(["to HSR Layout", "to Indiranagar", "to office", "late night ride"]),
                (provider[1], "Transport", "Cab & Auto", "cab,commute", provider[2], provider[3]))

    for day in scatter(2, 3, 27):
        led.add(year, month, day, rng.randint(260, 440), "debit",
                "POS/THIRD WAVE COFFEE ROASTERS HSR", "",
                ("Third Wave Coffee", "Food & Dining", "Cafe & Snacks", "coffee", "rag_direct", round(rng.uniform(0.87, 0.93), 2)))
    for day in scatter(rng.randint(2, 3), 2, 28):
        led.add(year, month, day, rng.choice([20, 30, 40, 60]), "debit",
                "UPI/CHAI WALA CORNER/PAYTM QR", "chai and samosa",
                (None, "Food & Dining", "Cafe & Snacks", "chai", "rag_prompted", round(rng.uniform(0.72, 0.85), 2)))

    led.add(year, month, rng.randint(8, 24), rng.randint(320, 780), "debit",
            "UPI/PHARMEASY/MEDICINES", "Monthly medicines",
            ("PharmEasy", "Health", "Pharmacy", "medicine", "rag_direct", round(rng.uniform(0.88, 0.93), 2)))

    led.add(year, month, rng.randint(9, 22), rng.choice([2000, 3000, 5000]), "debit",
            f"ATM WITHDRAWAL {rng.choice(['SBI KORAMANGALA', 'HDFC INDIRANAGAR'])}", "",
            (None, "Transfers", "ATM Withdrawal", "atm,cash", "manual", 1.0))

    for day in scatter(rng.randint(1, 2), 6, 26):
        merchant = rng.choice([
            ("AMAZON PAYMENTS INDIA PVT LTD", "Amazon", "Online Shopping", "rule", 0.95),
            ("FLIPKART INTERNET PVT LTD", "Flipkart", "Online Shopping", "rule", 0.95),
            ("MYNTRA DESIGNS PVT LTD", "Myntra", "Clothing & Apparel", "rag_prompted", round(rng.uniform(0.78, 0.88), 2)),
        ])
        led.add(year, month, day, rng.randint(450, 2900), "debit", merchant[0], "",
                (merchant[1], "Shopping", merchant[2], "online,shopping", merchant[3], merchant[4]))


def _stories(led: Ledger, months: list[tuple[int, int]]) -> None:
    """Month-specific arcs that make the insights panels tell a story."""
    (y0, m0), (y1, m1), (y2, m2), (y3, m3) = months

    # Oldest month: one-off purchases, the kind of long tail every ledger has.
    led.add(y0, m0, 18, 3200, "debit", "UPI/LENSKART/ORDER", "New glasses power change",
            ("Lenskart", "Health", "Doctor & Hospital", "eyewear", "manual", 1.0))
    led.add(y0, m0, 21, 999, "debit", "UDEMY COURSE PURCHASE", "",
            ("Udemy", "Education", "Online Courses", "course,learning", "llm", 0.79))
    led.add(y0, m0, 9, 750, "debit", "UPI/RAHUL.MEHTA@YBL", "lunch split last week",
            ("Rahul Mehta", "Transfers", "Peer Transfer", "split,lunch", "rule", 0.95))
    led.add(y0, m0, 15, 750, "credit", "UPI/RAHUL.MEHTA@YBL", "settled up lunch",
            ("Rahul Mehta", "Transfers", "Peer Transfer", "settled", "rule", 0.95))
    led.add(y0, m0, 26, 1100, "debit", "UPI/KETTO/DONATION", "Medical fundraiser",
            ("Ketto", "Gifts & Donations", "Charity", "donation,charity", "llm", 0.74))

    # Trip month: a Goa weekend as an expense group. Friends pay their shares
    # back a few days later; the group credits net the Travel category down.
    flight = led.add(y1, m1, 16, 8412, "debit", "MAKEMYTRIP/INDIGO FLIGHT BLR-GOI", "Goa flights for 3",
                     ("MakeMyTrip", "Travel", "Flights", "flight,goa-trip", "rag_prompted", 0.86))
    shack = led.add(y1, m1, 17, 2380, "debit", "POS/CURLIES BEACH SHACK ANJUNA", "",
                    ("Curlies Beach Shack", "Food & Dining", "Restaurants", "goa-trip", "llm", 0.68))
    scooter = led.add(y1, m1, 17, 800, "debit", "UPI/GOA BIKE RENTALS/PAYTM QR", "scooty 2 days",
                      ("Goa Bike Rentals", "Transport", "Cab & Auto", "rental,goa-trip", "llm", 0.61))
    hotel = led.add(y1, m1, 19, 7150, "debit", "MAKEMYTRIP/HOTEL BOOKING GOA", "Beach resort 3 nights",
                    ("MakeMyTrip", "Travel", "Hotels", "hotel,goa-trip", "rag_direct", 0.90))
    share_r = led.add(y1, m1, 21, 5600, "credit", "UPI/RAHUL.MEHTA@YBL", "goa share",
                      ("Rahul Mehta", "Transfers", "Peer Transfer", "goa-trip,settled", "rule", 0.95))
    share_n = led.add(y1, m1, 22, 5600, "credit", "UPI/NEHA.GUPTA@PAYTM", "goa trip share",
                      ("Neha Gupta", "Transfers", "Peer Transfer", "goa-trip,settled", "rule", 0.95))
    led.groups.append({
        "id": "demo_grp_goa",
        "name": "Goa trip with Rahul & Neha",
        "note": "Long weekend in Goa; I paid, both settled their shares.",
        "members": [
            (flight, "paid", "event", None),
            (hotel, "paid", "event", None),
            (shack, "paid", "event", None),
            (scooter, "paid", "event", None),
            (share_r, "received", "split", "demo_p02"),
            (share_n, "received", "split", "demo_p03"),
        ],
    })
    led.add(y1, m1, 24, 900, "debit", "UPI/PRACTO/DOCTOR CONSULT", "Post-trip fever consult",
            ("Practo", "Health", "Doctor & Hospital", "doctor,consultation", "rag_prompted", 0.83))

    # Last full month: an electronics splurge, a linked refund, a birthday
    # gift the user recategorized by hand, and an unlinked Swiggy refund.
    led.add(y2, m2, 8, 5990, "debit", "POS/CROMA ELECTRONICS PHOENIX MALL", "Mechanical keyboard",
            ("Croma", "Shopping", "Electronics", "electronics", "llm", 0.71))
    bought = led.add(y2, m2, 12, 2199, "debit", "AMAZON PAYMENTS INDIA PVT LTD", "Desk lamp",
                     ("Amazon", "Shopping", "Online Shopping", "online,shopping", "rule", 0.95))
    refund = led.add(y2, m2, 20, 2199, "credit", "UPI/AMAZON/REFUND", "Desk lamp return",
                     ("Amazon", "Income", "Refund", "refund", "manual", 1.0))
    if bought and refund:
        a, b = sorted([bought, refund])
        led.links.append((a, b, "refund", "Desk lamp returned"))
    led.add(y2, m2, 14, 2500, "debit", "UPI/PRIYA.SHARMA92@OKICICI", "birthday gift",
            ("Priya Sharma", "Gifts & Donations", "Personal Gifts", "gift,birthday", "manual", 1.0))
    led.add(y2, m2, 13, 900, "debit", "UPI/NEHA.GUPTA@PAYTM", "movie tickets share",
            ("Neha Gupta", "Transfers", "Peer Transfer", "split,movies", "rule", 0.95))
    cancelled = led.add(y2, m2, 9, 412, "debit", "UPI/SWIGGY/ORDER/9182736450", "order cancelled later",
                        ("Swiggy", "Food & Dining", "Food Delivery", "food,delivery", "rule", 0.95))
    if cancelled:
        led.add(y2, m2, 10, 412, "credit", "UPI/SWIGGY/REFUND", "cancelled order refund",
                ("Swiggy", "Income", "Refund", "refund", "rag_prompted", 0.87))
    led.add(y2, m2, 27, 700, "debit", "UPI/NATURALS SALON/YBL", "Haircut and grooming",
            ("Naturals Salon", "Personal Care", "Salon & Spa", "salon,grooming", "rag_prompted", 0.81))

    # Current month: a peer dinner split sits in the review backlog (added
    # here; the low-confidence rewrite happens in _recent_activity_policy).
    led.add(y3, m3, 4, 1350, "debit", "UPI/RAHUL.MEHTA@YBL", "dinner split",
            ("Rahul Mehta", "Transfers", "Peer Transfer", "split,dinner", "rule", 0.95))
    led.add(y3, m3, 5, 2450, "debit", "IRCTC TICKET BOOKING", "Train BLR to Chennai next month",
            ("IRCTC", "Travel", "Train", "train,travel", "rag_direct", 0.89))


def _fresh_unannotated(led: Ledger, today: date) -> None:
    """Never-seen merchants over the last two days, left unannotated on
    purpose: guaranteed fodder for a live auto-annotate no matter what day of
    the month the demo runs on."""
    from datetime import timedelta
    yesterday = today - timedelta(days=1)
    fresh = [
        (yesterday, 312, "UPI/BLUE TOKAI COFFEE/ORDER", "pour over beans"),
        (yesterday, 843, "UPI/LICIOUS/ORDER", "chicken and prawns"),
        (yesterday, 145, "UPI/BMRCL METRO/RECHARGE", "metro card top up"),
        (today, 460, "POS/GLENS BAKEHOUSE KORAMANGALA", ""),
        (today, 385, "UPI/APOLLO PHARMACY/BILL", "vitamins"),
        (today, 1240, "UPI/DUNZO/DELIVERY", "forgot charger at office"),
    ]
    for d, amount, desc, note in fresh:
        led.add(d.year, d.month, d.day, amount, "debit", desc, note, None)


def _review_backlog(led: Ledger, today: date) -> None:
    """Genuinely ambiguous recent transactions with low-confidence machine
    guesses: the curated review-queue backlog. Added after the recent-activity
    policy so their confidences and sources stay exactly as written."""
    from datetime import timedelta

    def day(offset: int) -> date:
        return today - timedelta(days=offset)

    backlog = [
        (8, 1200, "UPI/9988776655@OKAXIS", "annual thing",
         (None, "Uncategorized", None, "", "llm", 0.18)),
        (7, 650, "NEFT/REF789012/CLEARVIEW SERVICES LTD", "",
         ("Clearview Services", "Bills & Utilities", None, "", "llm", 0.22)),
        (6, 2000, "UPI/DECATHLON BLR/HDFCBANK", "running shoes",
         ("Decathlon", "Shopping", "General Retail", "sports", "llm", 0.55)),
        (5, 1100, "UPI/URBAN COMPANY/SERVICE", "deep cleaning",
         ("Urban Company", "Housing", "Home Repairs", "cleaning", "rag_prompted", 0.48)),
        (4, 890, "UPI/LENSKART/ORDER", "contact lens solution",
         ("Lenskart", "Health", "Pharmacy", "eyewear", "rag_prompted", 0.63)),
        (3, 540, "POS/BOOKS KAFE CHURCH STREET", "",
         ("Books Kafe", "Food & Dining", "Cafe & Snacks", "books,coffee", "llm", 0.44)),
        (2, 780, "UPI/IXIGO/BUS BOOKING", "bus to Mysore",
         ("Ixigo", "Travel", "Bus", "bus,travel", "rag_prompted", 0.52)),
    ]
    for offset, amount, desc, note, ann in backlog:
        d = day(offset)
        led.add(d.year, d.month, d.day, amount, "debit", desc, note, ann)


def _recent_activity_policy(led: Ledger, today: date, rng: random.Random) -> None:
    """Rewrite the last ten days of the ledger into a believable
    mid-pipeline state, and retire the older review backlog:

    - Commitments and exact-match merchants stay annotated everywhere (rules
      and learned rules fire instantly and deterministically for real).
    - Recent fuzzy transactions split into a low-confidence review backlog
      and a batch left unannotated for a live auto-annotate.
    - Older low-confidence items read as already reviewed: cold-LLM guesses
      became manual confirmations, RAG guesses settle above the threshold.
    """
    from datetime import timedelta
    window_start = (today - timedelta(days=9)).isoformat()
    trusted = ("rule", "learned_rule", "manual")
    recent = [t for t in led.txns
              if t["ann"] and t["ann"][4] not in trusted and t["date"] >= window_start]
    rng.shuffle(recent)
    review_count = max(3, len(recent) * 3 // 5)
    for t in recent[:review_count]:
        merchant, category, sub, tags, _source, _conf = t["ann"]
        t["ann"] = (merchant, category, sub, tags,
                    rng.choice(["llm", "rag_prompted"]), round(rng.uniform(0.35, 0.72), 2))
    for t in recent[review_count:]:
        t["ann"] = None

    for t in led.txns:
        if not t["ann"] or t["date"] >= window_start:
            continue
        merchant, category, sub, tags, source, conf = t["ann"]
        if source in trusted or conf >= 0.85:
            continue
        if source == "llm":
            t["ann"] = (merchant, category, sub, tags, "manual", 1.0)
        else:
            t["ann"] = (merchant, category, sub, tags, source, round(rng.uniform(0.86, 0.90), 2))


def build_ledger(today: date) -> Ledger:
    led = Ledger(today)
    months = [_month_add(today, off) for off in (-3, -2, -1, 0)]
    for i, (year, month) in enumerate(months):
        rng = random.Random(1000 + i)
        _fixed_monthly(led, year, month, rng, first_month=(i == 0))
        _variable_monthly(led, year, month, rng)
    _stories(led, months)
    _recent_activity_policy(led, today, random.Random(7))
    # After the policy on purpose: these carry hand-tuned states.
    _review_backlog(led, today)
    _fresh_unannotated(led, today)
    return led


def wipe(conn) -> None:
    conn.execute(
        "DELETE FROM transaction_links WHERE txn_a LIKE 'demo_%' OR txn_b LIKE 'demo_%'")
    conn.execute(
        "DELETE FROM transaction_group_members WHERE group_id LIKE 'demo_grp%'")
    conn.execute("DELETE FROM transaction_groups WHERE id LIKE 'demo_grp%'")
    conn.execute(
        "DELETE FROM annotations WHERE transaction_id IN "
        "(SELECT id FROM transactions WHERE statement_id LIKE 'demo_stmt%')")
    conn.execute(
        "DELETE FROM embedding_meta WHERE transaction_id IN "
        "(SELECT id FROM transactions WHERE statement_id LIKE 'demo_stmt%')")
    conn.execute(
        "DELETE FROM vec_items WHERE transaction_id IN "
        "(SELECT id FROM transactions WHERE statement_id LIKE 'demo_stmt%')")
    conn.execute("DELETE FROM transactions WHERE statement_id LIKE 'demo_stmt%'")
    conn.execute("DELETE FROM statements WHERE id LIKE 'demo_stmt%'")
    conn.execute("DELETE FROM people WHERE id LIKE 'demo_p%'")
    conn.commit()
    print("Wiped existing demo data.")


def seed(conn, today: date) -> None:
    led = build_ledger(today)

    # Statements, one per month.
    for stmt_id, ym in sorted({(t["statement_id"], t["month"]) for t in led.txns}):
        conn.execute(
            "INSERT OR IGNORE INTO statements (id, bank_name, parser_version, statement_month) VALUES (?, ?, ?, ?)",
            (stmt_id, "kotak", "1", ym))

    # Transactions in chronological order, with running balances computed as
    # we go. Within a day, insertion order (credits first on the 1st) holds.
    balance = OPENING_BALANCE
    txn_inserted = 0
    for t in sorted(led.txns, key=lambda t: (t["date"], t["id"])):
        balance += t["amount"] if t["dc"] == "credit" else -t["amount"]
        if conn.execute("SELECT 1 FROM transactions WHERE id = ?", (t["id"],)).fetchone():
            continue
        conn.execute(
            """INSERT INTO transactions
               (id, statement_id, txn_date, amount, debit_credit, raw_description,
                running_balance, upi_meta, counterparty_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t["id"], t["statement_id"], t["date"], t["amount"], t["dc"], t["desc"],
             round(balance, 2), json.dumps({"note": t["note"]}) if t["note"] else None,
             t["counterparty_key"]))
        txn_inserted += 1

    ann_inserted = 0
    for t in led.txns:
        if not t["ann"]:
            continue
        if conn.execute("SELECT 1 FROM annotations WHERE transaction_id = ?", (t["id"],)).fetchone():
            continue
        merchant, category, sub, tags, source, confidence = t["ann"]
        tags_json = json.dumps([tag for tag in tags.split(",") if tag]) if tags else "[]"
        conn.execute(
            """INSERT INTO annotations
               (id, transaction_id, merchant, category, subcategory, tags, confidence, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"ann_{t['id']}", t["id"], merchant, category, sub, tags_json, confidence, source))
        ann_inserted += 1

    for pid, name, upi, relationship in PEOPLE:
        conn.execute(
            "INSERT OR IGNORE INTO people (id, name, upi, relationship) VALUES (?, ?, ?, ?)",
            (pid, name, upi, relationship))

    for group in led.groups:
        conn.execute(
            "INSERT OR IGNORE INTO transaction_groups (id, name, note) VALUES (?, ?, ?)",
            (group["id"], group["name"], group["note"]))
        for txn_id, role, txn_type, people in group["members"]:
            if txn_id is None:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO transaction_group_members
                   (group_id, transaction_id, role, txn_type, people) VALUES (?, ?, ?, ?, ?)""",
                (group["id"], txn_id, role, txn_type, people))

    for i, (a, b, link_type, note) in enumerate(led.links):
        conn.execute(
            "INSERT OR IGNORE INTO transaction_links (id, txn_a, txn_b, link_type, note) VALUES (?, ?, ?, ?, ?)",
            (f"demo_link_{i:02d}", a, b, link_type, note))

    conn.commit()

    unannotated = conn.execute(
        """SELECT COUNT(*) FROM transactions t
           LEFT JOIN annotations a ON t.id = a.transaction_id
           WHERE t.statement_id LIKE 'demo_stmt%' AND a.id IS NULL""").fetchone()[0]
    low_conf = conn.execute(
        """SELECT COUNT(*) FROM annotations
           WHERE transaction_id LIKE 'demo_%' AND confidence < 0.85""").fetchone()[0]
    current_stmt = max(t["statement_id"] for t in led.txns)

    print(f"Seeded {txn_inserted} transactions across {len({t['month'] for t in led.txns})} months")
    print(f"Seeded {ann_inserted} annotations, {len(PEOPLE)} people, "
          f"{len(led.groups)} expense group(s), {len(led.links)} transaction link(s)")
    print()
    print(f"  {low_conf} annotations below the 0.85 threshold (review queue backlog)")
    print(f"  {unannotated} recent transactions unannotated (live auto-annotate fodder)")
    print()
    print("Serve the demo ledger:")
    print("  DB_PATH=data/demo.db PORT=8080 uv run python -m src")
    print()
    print("Live auto-annotate during the demo (needs Ollama + embeddings, see docs/demo.md):")
    print('  curl -X POST http://localhost:8080/api/annotations/auto-annotate \\')
    print(f'       -H "Content-Type: application/json" -d \'{{"statement_id": "{current_stmt}"}}\'')


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo ledger (default target: data/demo.db)")
    parser.add_argument("--db", default=DEFAULT_DB, help="target SQLite database path")
    parser.add_argument("--wipe", action="store_true", help="delete existing demo rows instead of seeding")
    args = parser.parse_args()

    conn = get_migrated_db(args.db)
    print(f"Target database: {args.db}")
    if args.wipe:
        wipe(conn)
    else:
        seed(conn, date.today())
    conn.close()


if __name__ == "__main__":
    main()
