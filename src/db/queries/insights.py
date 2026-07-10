"""Server-side aggregation for the Insights page.

Every number on the Insights page is computed here so the client receives one
small summary payload instead of the whole transaction table. Definitions:

- The verdict tiles are a cash view: In is every credit that arrived (except
  Self Transfers and Opening Balance artifacts), Out is every debit that left
  (except Self Transfers and Investments), Invested is Investment debits, and
  Kept is In - Out - Invested. The identity In = Out + Invested + Kept holds
  by construction, so the tiles can never contradict each other no matter how
  well credits are linked or annotated. Earned (true income: Income credits
  excluding the Refund and Opening Balance subcategories) rides along so the
  UI can disclose how much of In was income vs money coming back.
- The category breakdown uses net spend instead: spend excludes Self
  Transfers, Investments, Transfers (money to people, covered by the people
  ledger) and Income. Unannotated debits count as spend (Uncategorized).
- Net spend is gross debits minus offsets. Offsets come from, in order:
  credits linked to a debit via transaction_links, credit members of expense
  groups typed split/reimbursement/refund (allocated across the group's spend
  debits pro rata), unlinked credits annotated with a spend category (the
  label itself asserts "money coming back for this kind of spending"), and
  unlinked Income > Refund credits (attributed to the category of the most
  recent debit with the same counterparty, else to the most recent spend
  debit of the exact same amount; a credit attributable to no category nets
  nothing). Offsets land in the month the credit arrives (cash view).
- Offsets attributed to a specific debit are capped at that debit's amount,
  so a charge is never netted below zero across mechanisms (e.g. a grouped
  concert charge whose friends' shares came in and which was then fully
  refunded after cancellation). Counterparty-matched refunds are exempt from
  the cap because one refund credit can cover several past debits from the
  same merchant.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date
from statistics import median

NON_SPEND_CATEGORIES = ("Self Transfers", "Investments", "Transfers", "Income")

# Credit-side link/group types that offset spending rather than being income.
OFFSET_TYPES = ("split", "reimbursement", "refund")

# Duplicate subcategory labels produced by taxonomy drift in the annotation
# pipeline. Display-level shim; the durable fix is at annotation time.
SUBCATEGORY_ALIASES = {
    "cafes": "Cafe & Snacks",
    "restaurant": "Restaurants",
    "dining": "Restaurants",
    "concerts": "Events & Concerts",
}

# A recurring charge whose last occurrence is older than this (relative to the
# newest transaction in the ledger) is reported as stopped, not active.
STOPPED_AFTER_DAYS = 45


def _prev_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def _canonical_subcategory(sub: str | None) -> str:
    if not sub:
        return "Other"
    return SUBCATEGORY_ALIASES.get(sub.strip().lower(), sub.strip())


def _is_spend(category: str | None) -> bool:
    return (category or "Uncategorized") not in NON_SPEND_CATEGORIES


def _merge_merchant_keys(keys: list[str]) -> dict[str, str]:
    """Map each merchant key to a canonical one by word-boundary prefix merge.

    Bank feeds truncate and decorate the same counterparty differently
    (ZOMATO vs ZOMATO LIMITED, ZEPTO vs ZEPTO MARKETPLA). Rather than a
    per-merchant alias list, merge any key that extends another key at a word
    boundary into the shorter key, requiring at least 5 characters so short
    generic prefixes never swallow unrelated merchants.
    """
    canonical: dict[str, str] = {}
    for key in sorted(set(keys), key=len):
        target = key
        for existing in canonical.values():
            if (
                len(existing) >= 5
                and key.startswith(existing)
                and (len(key) == len(existing) or key[len(existing)] == " ")
            ):
                target = existing
                break
        canonical[key] = target
    return canonical


def _spend_offsets(conn: sqlite3.Connection) -> tuple[dict, set[str]]:
    """Compute spend offsets across the full ledger.

    Returns (category_offsets, used_credit_ids):
    - category_offsets: {(month, category): amount} for offsets attributable
      to a spend category. A credit attributable to no category nets nothing;
      it still shows up in the verdict's cash view as money in.
    - used_credit_ids: credit transactions consumed as offsets, so other
      panels (the people ledger) can avoid counting a settled share twice.
    """
    category_offsets: dict[tuple[str, str], float] = defaultdict(float)
    used_credit_ids: set[str] = set()

    # Remaining offsettable amount per debit id, seeded lazily with the debit
    # amount. Every offset attributed to a specific debit consumes capacity so
    # the debit is never netted below zero across mechanisms.
    remaining: dict[str, float] = {}

    def _capacity(debit_id: str, debit_amount: float) -> float:
        return remaining.setdefault(debit_id, debit_amount)

    def _consume(debit_id: str, debit_amount: float, credit_amount: float) -> float:
        take = min(credit_amount, max(_capacity(debit_id, debit_amount), 0.0))
        remaining[debit_id] -= take
        return take

    # 1. Explicit links: a refund/reimbursement/split credit against a debit.
    link_rows = conn.execute(
        """
        SELECT l.link_type,
               ta.id AS a_id, ta.debit_credit AS a_dc, ta.amount AS a_amount,
               strftime('%Y-%m', ta.txn_date) AS a_month, aa.category AS a_category,
               tb.id AS b_id, tb.debit_credit AS b_dc, tb.amount AS b_amount,
               strftime('%Y-%m', tb.txn_date) AS b_month, ab.category AS b_category
        FROM transaction_links l
        JOIN transactions ta ON ta.id = l.txn_a
        JOIN transactions tb ON tb.id = l.txn_b
        LEFT JOIN annotations aa ON aa.transaction_id = ta.id
        LEFT JOIN annotations ab ON ab.transaction_id = tb.id
        WHERE l.link_type IN ('split', 'reimbursement', 'refund')
        """
    ).fetchall()
    for row in link_rows:
        if row["a_dc"] == "credit" and row["b_dc"] == "debit":
            credit, debit = ("a", "b")
        elif row["b_dc"] == "credit" and row["a_dc"] == "debit":
            credit, debit = ("b", "a")
        else:
            continue
        used_credit_ids.add(row[f"{credit}_id"])
        amount = _consume(
            row[f"{debit}_id"], row[f"{debit}_amount"], row[f"{credit}_amount"]
        )
        if amount <= 0:
            continue
        debit_category = row[f"{debit}_category"]
        if _is_spend(debit_category):
            category_offsets[(row[f"{credit}_month"], debit_category or "Uncategorized")] += amount

    # 2. Group credits: shares friends paid back inside an expense group,
    #    allocated pro rata across the group's spend debits.
    member_rows = conn.execute(
        """
        SELECT gm.group_id, gm.txn_type, t.id, t.amount, t.debit_credit,
               strftime('%Y-%m', t.txn_date) AS month, a.category
        FROM transaction_group_members gm
        JOIN transactions t ON t.id = gm.transaction_id
        LEFT JOIN annotations a ON a.transaction_id = t.id
        """
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in member_rows:
        groups[row["group_id"]].append(row)
    for members in groups.values():
        spend_debits = [
            m for m in members
            if m["debit_credit"] == "debit" and _is_spend(m["category"])
        ]
        credits = [
            m for m in members
            if m["debit_credit"] == "credit" and m["txn_type"] in OFFSET_TYPES
        ]
        for cred in credits:
            used_credit_ids.add(cred["id"])
            if not spend_debits:
                continue
            # Allocate against remaining capacity so shares plus a later
            # refund can't offset the same debit twice. Anything beyond the
            # group's remaining capacity is money owed onward, not spend
            # coming back, so it nets nothing.
            capacities = [
                (deb, max(_capacity(deb["id"], deb["amount"]), 0.0))
                for deb in spend_debits
            ]
            total_capacity = sum(cap for _, cap in capacities)
            if total_capacity <= 0:
                continue
            attributable = min(cred["amount"], total_capacity)
            for deb, cap in capacities:
                if cap <= 0:
                    continue
                share = attributable * cap / total_capacity
                remaining[deb["id"]] -= share
                category_offsets[(cred["month"], deb["category"] or "Uncategorized")] += share

    # 3. Unlinked credits nobody grouped or linked. A credit annotated with a
    #    spend category nets that category directly - the label already
    #    asserts it is money coming back for spending. Income > Refund
    #    credits are attributed to the category of the most recent debit from
    #    the same counterparty when one exists, else to the most recent spend
    #    debit of the exact same amount (capped at that debit's remaining
    #    capacity - a fully-shared charge refunded after cancellation nets
    #    only the user's own share); otherwise the credit nets nothing and
    #    the verdict's cash view already counts it as money in.
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    credit_rows = conn.execute(
        f"""
        SELECT t.id, t.amount, t.counterparty_key, t.txn_date,
               strftime('%Y-%m', t.txn_date) AS month, a.category
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'credit'
          AND ((a.category = 'Income' AND a.subcategory = 'Refund')
               OR a.category NOT IN ({placeholders}))
        """,
        NON_SPEND_CATEGORIES,
    ).fetchall()
    for row in credit_rows:
        if row["id"] in used_credit_ids:
            continue
        used_credit_ids.add(row["id"])
        if row["category"] != "Income":
            category_offsets[(row["month"], row["category"])] += row["amount"]
            continue
        if row["counterparty_key"]:
            match = conn.execute(
                """
                SELECT a.category
                FROM transactions t
                JOIN annotations a ON a.transaction_id = t.id
                WHERE t.debit_credit = 'debit' AND t.counterparty_key = ?
                  AND t.txn_date <= ?
                ORDER BY t.txn_date DESC LIMIT 1
                """,
                (row["counterparty_key"], row["txn_date"]),
            ).fetchone()
            if match and _is_spend(match["category"]):
                category_offsets[(row["month"], match["category"] or "Uncategorized")] += row["amount"]
                continue
        amount_match = conn.execute(
            f"""
            SELECT t.id, t.amount, a.category
            FROM transactions t
            LEFT JOIN annotations a ON a.transaction_id = t.id
            WHERE t.debit_credit = 'debit' AND t.amount = ?
              AND t.txn_date <= ? AND t.txn_date >= date(?, '-60 days')
              AND COALESCE(a.category, 'Uncategorized') NOT IN ({placeholders})
            ORDER BY t.txn_date DESC LIMIT 1
            """,
            (row["amount"], row["txn_date"], row["txn_date"], *NON_SPEND_CATEGORIES),
        ).fetchone()
        if amount_match:
            take = _consume(amount_match["id"], amount_match["amount"], row["amount"])
            if take > 0:
                category_offsets[
                    (row["month"], amount_match["category"] or "Uncategorized")
                ] += take

    return dict(category_offsets), used_credit_ids


def _verdict(conn: sqlite3.Connection, month: str) -> dict:
    """Cash-view tiles for one month; see the module docstring for the model.

    No linking, netting or heuristics feed these numbers, so a missed
    reimbursement can never make the tiles contradict each other - a friend's
    unmatched payback simply shows up inside In (and other_in).
    """
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN t.debit_credit = 'credit'
              AND COALESCE(a.category, '') != 'Self Transfers'
              AND NOT (COALESCE(a.category, '') = 'Income'
                       AND COALESCE(a.subcategory, '') = 'Opening Balance')
              THEN t.amount END), 0) AS money_in,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'credit' AND a.category = 'Income'
              AND COALESCE(a.subcategory, '') NOT IN ('Refund', 'Opening Balance')
              THEN t.amount END), 0) AS earned,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit'
              AND COALESCE(a.category, '') NOT IN ('Self Transfers', 'Investments')
              THEN t.amount END), 0) AS money_out,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit' AND a.category = 'Investments'
              THEN t.amount END), 0) AS invested
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE strftime('%Y-%m', t.txn_date) = ?
        """,
        (month,),
    ).fetchone()
    money_in, money_out = row["money_in"], row["money_out"]
    invested = row["invested"]
    kept = money_in - money_out - invested
    return {
        "money_in": round(money_in, 2),
        "money_out": round(money_out, 2),
        "invested": round(invested, 2),
        "kept": round(kept, 2),
        "kept_rate": round(kept / money_in, 4) if money_in > 0 else None,
        "earned": round(row["earned"], 2),
        "other_in": round(money_in - row["earned"], 2),
    }


def _categories_for_month(conn: sqlite3.Connection, month: str, cat_offsets: dict) -> dict[str, dict]:
    """Net spend per category for one month, with a subcategory drill-down."""
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    rows = conn.execute(
        f"""
        SELECT COALESCE(a.category, 'Uncategorized') AS category,
               a.subcategory, COUNT(*) AS n, SUM(t.amount) AS total
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND strftime('%Y-%m', t.txn_date) = ?
          AND COALESCE(a.category, 'Uncategorized') NOT IN ({placeholders})
        GROUP BY 1, 2
        """,
        (month, *NON_SPEND_CATEGORIES),
    ).fetchall()
    categories: dict[str, dict] = {}
    for row in rows:
        cat = categories.setdefault(
            row["category"],
            {"gross": 0.0, "count": 0, "subcategories": defaultdict(lambda: {"total": 0.0, "count": 0})},
        )
        cat["gross"] += row["total"]
        cat["count"] += row["n"]
        sub = cat["subcategories"][_canonical_subcategory(row["subcategory"])]
        sub["total"] += row["total"]
        sub["count"] += row["n"]
    # Offsets can land in a month where the category had no debit (e.g. a
    # refund arriving a month later); surface those as negative-net rows.
    for (m, category), amount in cat_offsets.items():
        if m == month and category not in categories:
            categories[category] = {
                "gross": 0.0, "count": 0,
                "subcategories": defaultdict(lambda: {"total": 0.0, "count": 0}),
            }
    for category, data in categories.items():
        data["offsets"] = round(cat_offsets.get((month, category), 0.0), 2)
        data["net"] = round(data["gross"] - data["offsets"], 2)
        data["gross"] = round(data["gross"], 2)
        data["subcategories"] = sorted(
            (
                {"name": name, "total": round(s["total"], 2), "count": s["count"]}
                for name, s in data["subcategories"].items()
            ),
            key=lambda s: -s["total"],
        )
    return categories


def _recurring(conn: sqlite3.Connection, latest_date: str) -> list[dict]:
    """Detect committed money: charges of the same amount from the same
    counterparty across months (SIPs, subscriptions, rent-like transfers).

    A (counterparty, rounded amount) unit is recurring when it shows up in 3+
    months covering at least 60% of its span, or in 2+ months for categories
    that are commitments by definition (Subscriptions, Investments). Units
    charged more than ~1.5 times per active month are habitual spending at a
    fixed price point (canteens, metro fares), not commitments, and are dropped.
    """
    rows = conn.execute(
        """
        SELECT t.counterparty_key AS key, CAST(ROUND(t.amount) AS INTEGER) AS amount,
               t.txn_date, strftime('%Y-%m', t.txn_date) AS month,
               a.category, a.merchant
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND t.counterparty_key IS NOT NULL
          AND COALESCE(a.category, '') NOT IN ('Transfers', 'Self Transfers')
        ORDER BY t.txn_date
        """
    ).fetchall()
    units: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        units[(row["key"], row["amount"])].append(row)

    def month_index(month: str) -> int:
        return int(month[:4]) * 12 + int(month[5:7])

    items = []
    for (key, amount), charges in units.items():
        months_seen = sorted({c["month"] for c in charges})
        span = month_index(months_seen[-1]) - month_index(months_seen[0]) + 1
        coverage = len(months_seen) / span
        per_month = len(charges) / len(months_seen)
        category = charges[-1]["category"]
        is_commitment_category = category in ("Subscriptions", "Investments")
        qualifies = per_month <= 1.5 and (
            (len(months_seen) >= 3 and coverage >= 0.6)
            or (len(months_seen) >= 2 and is_commitment_category)
        )
        if not qualifies:
            continue
        merchant_names = Counter(c["merchant"] for c in charges if c["merchant"])
        dates = [c["txn_date"] for c in charges]
        gaps = [
            (_days_between(dates[i - 1], dates[i]))
            for i in range(1, len(dates))
        ]
        last_date = dates[-1]
        items.append({
            "name": merchant_names.most_common(1)[0][0] if merchant_names else key.title(),
            "category": category,
            "amount": amount,
            "months_seen": len(months_seen),
            "months_span": span,
            "cadence": "monthly" if gaps and 25 <= median(gaps) <= 35 else "irregular",
            "last_date": last_date,
            "active": _days_between(last_date, latest_date) <= STOPPED_AFTER_DAYS,
        })
    items.sort(key=lambda i: -i["amount"])
    return items


def _days_between(earlier: str, later: str) -> int:
    d1 = date.fromisoformat(earlier[:10])
    d2 = date.fromisoformat(later[:10])
    return (d2 - d1).days


def _people_ledger(conn: sqlite3.Connection, settled_ids: set[str]) -> dict:
    """Net position per person over the full history of Transfers.

    Transactions carry no person foreign key; the annotation pipeline labels
    person payments as Transfers with the person's short name as merchant.
    Match each Transfers row to a person by exact merchant name, then by the
    person's name/UPI appearing in the counterparty or merchant string
    (4+ characters, so short names like "ma" only match exactly).

    Credits already consumed as spend offsets (a friend paying back their
    share of a linked/grouped expense) are settlements of that expense, not
    money the person gave the user, so they are excluded here. ATM
    withdrawals live under Transfers in the taxonomy but are cash to self,
    not money between people, so they are excluded too.
    """
    people = conn.execute("SELECT id, name, upi, relationship FROM people").fetchall()
    rows = conn.execute(
        """
        SELECT t.id, t.amount, t.debit_credit, t.txn_date,
               LOWER(COALESCE(t.counterparty_key, '')) AS cpk,
               LOWER(COALESCE(a.merchant, '')) AS merchant
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        WHERE a.category = 'Transfers'
          AND COALESCE(a.subcategory, '') != 'ATM Withdrawal'
        """
    ).fetchall()
    rows = [r for r in rows if r["id"] not in settled_ids]

    def match(row) -> str | None:
        for p in people:
            if row["merchant"] and row["merchant"] == p["name"].lower():
                return p["id"]
        for p in people:
            for token in (p["name"], p["upi"]):
                token = (token or "").lower()
                if len(token) >= 4 and (token in row["cpk"] or token in row["merchant"]):
                    return p["id"]
        return None

    stats = {p["id"]: {"sent": 0.0, "received": 0.0, "count": 0, "last_date": None} for p in people}
    unmatched = {"sent": 0.0, "received": 0.0, "count": 0}
    for row in rows:
        person_id = match(row)
        if person_id is None:
            direction = "sent" if row["debit_credit"] == "debit" else "received"
            unmatched[direction] += row["amount"]
            unmatched["count"] += 1
            continue
        entry = stats[person_id]
        entry["sent" if row["debit_credit"] == "debit" else "received"] += row["amount"]
        entry["count"] += 1
        if entry["last_date"] is None or row["txn_date"] > entry["last_date"]:
            entry["last_date"] = row["txn_date"]

    items = []
    for p in people:
        entry = stats[p["id"]]
        if entry["count"] == 0:
            continue
        items.append({
            "id": p["id"],
            "name": p["name"],
            "relationship": p["relationship"],
            "sent": round(entry["sent"], 2),
            "received": round(entry["received"], 2),
            "net": round(entry["received"] - entry["sent"], 2),
            "count": entry["count"],
            "last_date": entry["last_date"],
        })
    items.sort(key=lambda i: -(abs(i["net"])))
    for key in ("sent", "received"):
        unmatched[key] = round(unmatched[key], 2)
    return {"items": items, "unmatched": unmatched}


def _merchants(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Top merchants for the month on canonical keys, with count and avg ticket."""
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    rows = conn.execute(
        f"""
        SELECT UPPER(COALESCE(t.counterparty_key, a.merchant)) AS key,
               a.merchant, t.amount
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND strftime('%Y-%m', t.txn_date) = ?
          AND COALESCE(a.category, 'Uncategorized') NOT IN ({placeholders})
          AND COALESCE(t.counterparty_key, a.merchant) IS NOT NULL
        """,
        (month, *NON_SPEND_CATEGORIES),
    ).fetchall()
    canonical = _merge_merchant_keys([r["key"] for r in rows])
    agg: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0, "names": Counter()})
    for row in rows:
        entry = agg[canonical[row["key"]]]
        entry["total"] += row["amount"]
        entry["count"] += 1
        if row["merchant"]:
            entry["names"][row["merchant"]] += 1
    items = [
        {
            "name": entry["names"].most_common(1)[0][0] if entry["names"] else key.title(),
            "total": round(entry["total"], 2),
            "count": entry["count"],
            "avg": round(entry["total"] / entry["count"], 2),
        }
        for key, entry in agg.items()
    ]
    items.sort(key=lambda i: -i["total"])
    return items[:8]


def _balance_series(conn: sqlite3.Connection, max_points: int = 240) -> dict:
    """Running balance over the full history for a single account, one point per
    day (last balance of the day), downsampled evenly if the history outgrows
    max_points.

    running_balance is a per-account chain: each statement continues its own
    account's balance. Interleaving two real accounts by date produces a sawtooth
    that represents neither, so the series is scoped to one account.

    That scope follows the bank, not the account_ref string. account_ref is
    best-effort header extraction (see StatementParser.extract_account_ref): the
    same account can arrive with a number on some statements and NULL on others
    when older statements omit the header. Treating each distinct account_ref as
    its own account would then splinter one real account into fragments and drop
    whichever months don't match the busiest fragment. So within the primary
    bank, an account_ref only splits the chain when the bank genuinely holds
    two-plus *identified* accounts; a NULL ("unknown") folds into the single
    identified account rather than spawning a phantom second one.

    Returns {"account": <label or None>, "series": [{date, balance}, ...]}.
    """
    bank = conn.execute(
        """
        SELECT s.bank_name AS bank_name, COUNT(*) AS n
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        WHERE t.running_balance IS NOT NULL
        GROUP BY s.bank_name
        ORDER BY n DESC, s.bank_name
        LIMIT 1
        """
    ).fetchone()
    if bank is None:
        return {"account": None, "series": []}
    bank_name = bank["bank_name"]

    # Identified accounts within this bank, busiest first. A blank/NULL
    # account_ref is "unknown", not an account, so it is excluded here.
    accounts = conn.execute(
        """
        SELECT s.account_ref AS account_ref, COUNT(*) AS n
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        WHERE t.running_balance IS NOT NULL AND s.bank_name = ?
          AND s.account_ref IS NOT NULL AND s.account_ref != ''
        GROUP BY s.account_ref
        ORDER BY n DESC, s.account_ref
        """,
        (bank_name,),
    ).fetchall()

    if len(accounts) <= 1:
        # One (or zero) identified account: the whole bank is one chain, so the
        # unknown-account statements chain with the identified one.
        account_ref = accounts[0]["account_ref"] if accounts else None
        where, params = "s.bank_name = ?", (bank_name,)
    else:
        # Genuinely multiple accounts at this bank: scope to the busiest and
        # exclude unknowns, which can't be attributed to a single account.
        account_ref = accounts[0]["account_ref"]
        where = "s.bank_name = ? AND s.account_ref = ?"
        params = (bank_name, account_ref)

    rows = conn.execute(
        f"""
        SELECT t.txn_date AS date, t.running_balance AS balance
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        WHERE t.running_balance IS NOT NULL AND {where}
        ORDER BY t.txn_date, t.rowid
        """,
        params,
    ).fetchall()
    by_day: dict[str, float] = {}
    for row in rows:
        by_day[row["date"]] = row["balance"]
    series = [{"date": d, "balance": b} for d, b in sorted(by_day.items())]
    if len(series) > max_points:
        step = -(-len(series) // max_points)
        sampled = series[::step]
        if sampled[-1]["date"] != series[-1]["date"]:
            sampled.append(series[-1])
        series = sampled
    return series


def _unexplained(conn: sqlite3.Connection, month: str) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND strftime('%Y-%m', t.txn_date) = ?
          AND (a.id IS NULL OR a.category = 'Miscellaneous')
        """,
        (month,),
    ).fetchone()
    return {"count": row["n"], "total": round(row["total"], 2)}


def _annotation_coverage(conn: sqlite3.Connection, month: str) -> dict:
    """How much of the month the pipeline has touched. annotated == 0 with
    transactions present means auto-annotation never ran for this month; the
    dashboard uses that to hold back insights until the user starts it."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS total,
               COUNT(DISTINCT a.transaction_id) AS annotated
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE strftime('%Y-%m', t.txn_date) = ?
        """,
        (month,),
    ).fetchone()
    return {"total": row["total"], "annotated": row["annotated"]}


def summarize_insights(conn: sqlite3.Connection, month: str | None = None) -> dict:
    months = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT strftime('%Y-%m', txn_date) FROM transactions ORDER BY 1"
        )
    ]
    if not months:
        return {"months": [], "month": None}
    if month is None or month not in months:
        month = months[-1]
    prev = _prev_month(month)

    cat_offsets, settled_ids = _spend_offsets(conn)

    current = _categories_for_month(conn, month, cat_offsets)
    previous = _categories_for_month(conn, prev, cat_offsets)

    categories = [
        {
            "category": name,
            "gross": data["gross"],
            "net": data["net"],
            "offsets": data["offsets"],
            "count": data["count"],
            "prev_net": previous.get(name, {}).get("net", 0.0),
            "delta": round(data["net"] - previous.get(name, {}).get("net", 0.0), 2),
            "subcategories": data["subcategories"],
        }
        for name, data in current.items()
    ]
    categories.sort(key=lambda c: -c["net"])

    deltas = []
    for name in set(current) | set(previous):
        cur_net = current.get(name, {}).get("net", 0.0)
        prev_net = previous.get(name, {}).get("net", 0.0)
        delta = round(cur_net - prev_net, 2)
        if abs(delta) >= 1:
            deltas.append({
                "category": name, "delta": delta,
                "current": cur_net, "previous": prev_net,
            })
    deltas.sort(key=lambda d: -abs(d["delta"]))

    latest_date = conn.execute("SELECT MAX(txn_date) FROM transactions").fetchone()[0]

    return {
        "month": month,
        "prev_month": prev,
        "months": months,
        "verdict": {
            **_verdict(conn, month),
            "prev": _verdict(conn, prev),
        },
        "what_changed": deltas[:3],
        "categories": categories,
        "recurring": _recurring(conn, latest_date),
        "people": _people_ledger(conn, settled_ids),
        "merchants": _merchants(conn, month),
        "balance": _balance_series(conn),
        "unexplained": _unexplained(conn, month),
        "annotation": _annotation_coverage(conn, month),
    }
