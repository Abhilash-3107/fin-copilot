"""Server-side aggregation for the Insights page.

Every number on the Insights page is computed here so the client receives one
small summary payload instead of the whole transaction table. Definitions:

- Spend excludes Self Transfers (money staying with the user), Investments
  (allocation, not consumption), Transfers (money to people, covered by the
  people ledger) and Income. Unannotated debits count as spend (Uncategorized).
- Earned is Income credits excluding the Refund and Opening Balance
  subcategories; refunds offset spend instead of counting as income.
- Net spend is gross debits minus offsets. Offsets come from, in order:
  credits linked to a debit via transaction_links, credit members of expense
  groups typed split/reimbursement/refund (allocated across the group's spend
  debits pro rata), and unlinked Income > Refund credits (attributed to the
  category of the most recent debit with the same counterparty, else to the
  month only). Offsets land in the month the credit arrives (cash view).
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


def _spend_offsets(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Compute spend offsets across the full ledger.

    Returns (category_offsets, month_offsets):
    - category_offsets: {(month, category): amount} for offsets attributable
      to a spend category.
    - month_offsets: {month: amount} for offsets where no category is knowable.
    """
    category_offsets: dict[tuple[str, str], float] = defaultdict(float)
    month_offsets: dict[str, float] = defaultdict(float)
    used_credit_ids: set[str] = set()

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
        amount = row[f"{credit}_amount"]
        used_credit_ids.add(row[f"{credit}_id"])
        debit_category = row[f"{debit}_category"]
        if _is_spend(debit_category):
            category_offsets[(row[f"{credit}_month"], debit_category or "Uncategorized")] += amount
        else:
            month_offsets[row[f"{credit}_month"]] += amount

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
        debit_total = sum(m["amount"] for m in spend_debits)
        for cred in credits:
            used_credit_ids.add(cred["id"])
            if debit_total <= 0:
                month_offsets[cred["month"]] += cred["amount"]
                continue
            attributable = min(cred["amount"], debit_total)
            for deb in spend_debits:
                share = attributable * deb["amount"] / debit_total
                category_offsets[(cred["month"], deb["category"] or "Uncategorized")] += share
            leftover = cred["amount"] - attributable
            if leftover > 0:
                month_offsets[cred["month"]] += leftover

    # 3. Unlinked refunds: Income > Refund credits nobody linked. Attribute to
    #    the category of the most recent debit from the same counterparty when
    #    one exists; otherwise only the month total can be corrected.
    refund_rows = conn.execute(
        """
        SELECT t.id, t.amount, t.counterparty_key, t.txn_date,
               strftime('%Y-%m', t.txn_date) AS month
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'credit'
          AND a.category = 'Income' AND a.subcategory = 'Refund'
        """
    ).fetchall()
    for row in refund_rows:
        if row["id"] in used_credit_ids:
            continue
        category = None
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
                category = match["category"] or "Uncategorized"
        if category:
            category_offsets[(row["month"], category)] += row["amount"]
        else:
            month_offsets[row["month"]] += row["amount"]

    return dict(category_offsets), dict(month_offsets)


def _verdict(conn: sqlite3.Connection, month: str, cat_offsets: dict, month_offsets: dict) -> dict:
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    row = conn.execute(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.debit_credit = 'credit' AND a.category = 'Income'
              AND COALESCE(a.subcategory, '') NOT IN ('Refund', 'Opening Balance')
              THEN t.amount END), 0) AS earned,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit'
              AND COALESCE(a.category, 'Uncategorized') NOT IN ({placeholders})
              THEN t.amount END), 0) AS spent_gross,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit' AND a.category = 'Investments'
              THEN t.amount END), 0) AS invested
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE strftime('%Y-%m', t.txn_date) = ?
        """,
        (*NON_SPEND_CATEGORIES, month),
    ).fetchone()
    offsets = sum(v for (m, _), v in cat_offsets.items() if m == month)
    offsets += month_offsets.get(month, 0.0)
    earned, spent_gross = row["earned"], row["spent_gross"]
    spent = spent_gross - offsets
    saved = earned - spent
    return {
        "earned": round(earned, 2),
        "spent": round(spent, 2),
        "spent_gross": round(spent_gross, 2),
        "offsets": round(offsets, 2),
        "invested": round(row["invested"], 2),
        "saved": round(saved, 2),
        "savings_rate": round(saved / earned, 4) if earned > 0 else None,
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


def _people_ledger(conn: sqlite3.Connection) -> dict:
    """Net position per person over the full history of Transfers.

    Transactions carry no person foreign key; the annotation pipeline labels
    person payments as Transfers with the person's short name as merchant.
    Match each Transfers row to a person by exact merchant name, then by the
    person's name/UPI appearing in the counterparty or merchant string
    (4+ characters, so short names like "ma" only match exactly).
    """
    people = conn.execute("SELECT id, name, upi, relationship FROM people").fetchall()
    rows = conn.execute(
        """
        SELECT t.amount, t.debit_credit, t.txn_date,
               LOWER(COALESCE(t.counterparty_key, '')) AS cpk,
               LOWER(COALESCE(a.merchant, '')) AS merchant
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        WHERE a.category = 'Transfers'
        """
    ).fetchall()

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


def _balance_series(conn: sqlite3.Connection, max_points: int = 240) -> list[dict]:
    """Running balance over the full history, one point per day (last balance
    of the day), downsampled evenly if the history outgrows max_points."""
    rows = conn.execute(
        """
        SELECT t.txn_date AS date, t.running_balance AS balance
        FROM transactions t
        WHERE t.running_balance IS NOT NULL
        ORDER BY t.txn_date, t.rowid
        """
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

    cat_offsets, month_offsets = _spend_offsets(conn)

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
            **_verdict(conn, month, cat_offsets, month_offsets),
            "prev": _verdict(conn, prev, cat_offsets, month_offsets),
        },
        "what_changed": deltas[:3],
        "categories": categories,
        "recurring": _recurring(conn, latest_date),
        "people": _people_ledger(conn),
        "merchants": _merchants(conn, month),
        "balance": _balance_series(conn),
        "unexplained": _unexplained(conn, month),
    }
