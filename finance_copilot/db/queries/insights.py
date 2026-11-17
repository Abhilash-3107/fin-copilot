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
  Because Out is gross and the rows are net, the payload also carries the
  reconciling rows (Transfers and cash, Refunds and paybacks taken off the rows above, other debits), so
  rows + extras always sum to Out exactly (breakdown.total == verdict.money_out).
- "Not sorted" has one definition (common.unsorted_sql): a debit with no
  annotation or whose category is NULL or Uncategorized. The unexplained
  figure, the Uncategorized breakdown row and the Transactions filter all use it.
- Net spend is gross debits minus offsets. Offsets come from, in order:
  credits linked to a debit via transaction_links, credit members of expense
  groups typed split/reimbursement/refund (allocated across the group's spend
  debits pro rata), unlinked credits annotated with a spend category (the
  label itself asserts "money coming back for this kind of spending"), and
  unlinked Income > Refund credits (attributed to the category of the most
  recent debit with the same counterparty, else to the most recent spend
  debit of the exact same amount; a credit attributable to no category nets
  nothing). Offsets land on the day the credit arrives (cash view), so they
  sum over any date range.
- Offsets attributed to a specific debit are capped at that debit's amount,
  so a charge is never netted below zero across mechanisms (e.g. a grouped
  concert charge whose friends' shares came in and which was then fully
  refunded after cancellation). Counterparty-matched refunds are exempt from
  the cap because one refund credit can cover several past debits from the
  same merchant.
- Everything is computed over a date range, not a month string, so a month
  still in progress is compared like for like: when the ledger only covers a
  month up to day D, the comparison window is days 1..D of the previous month,
  and there is no comparison at all when the ledger does not reach back to the
  start of that window (see _period_windows).
"""
from __future__ import annotations

import calendar
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from statistics import median

from finance_copilot.db.queries.accounts import account_label
from finance_copilot.db.queries.common import UNCATEGORIZED, unsorted_sql
from finance_copilot.db.queries.payee import payee_labels
from finance_copilot.db.queries.transactions import payment_months

NON_SPEND_CATEGORIES = ("Self Transfers", "Investments", "Transfers", "Income")

# Debits that never count as Out (the tiles are a cash view of money that left
# for good: moving money between own accounts or into investments is not spend).
OUT_EXCLUDED_CATEGORIES = ("Self Transfers", "Investments")

# Credit-side link/group types that offset spending rather than being income.
OFFSET_TYPES = ("split", "reimbursement", "refund")

# A recurring charge whose last occurrence is older than this (relative to the
# newest transaction in the ledger) is reported as stopped, not active.
STOPPED_AFTER_DAYS = 45


def _prev_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def _month_bounds(month: str) -> tuple[date, date]:
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def _period_windows(conn: sqlite3.Connection, month: str) -> tuple[dict, dict | None]:
    """How far the ledger covers `month`, and the like-for-like window to compare with.

    Returns (period, comparison). period = {month, start, end, covered_through,
    partial}: start/end are the calendar bounds of the month, covered_through is
    the last day the ledger has data for. covered_through is the latest of the
    newest transaction in the month and the end of any statement period that
    reaches into it (capped at the month's last day); a month that later data
    follows is fully covered. A month is partial when covered_through is before
    its last day.

    comparison = {month, start, end} is the previous month when `month` is
    complete, or only its first N days when `month` is covered to day N, so a
    half month is never scored against a whole one. It is None when the
    previous month has no transactions at all (a gap is not a month of zero
    spend), or when the ledger's coverage does not reach back to the start of
    that window (the first month of a ledger has nothing to compare with).
    A month with data both before and after it counts as covered.

    Statement periods equal the first and last transaction date of the
    statement (see ingest), so a finished month whose last transaction is on
    the 28th still looks covered only to the 28th when nothing follows it;
    the caller decides, from the clock, whether the month is really in
    progress. The comparison over covered days is like for like either way.
    """
    first, last = _month_bounds(month)
    first_iso, last_iso = first.isoformat(), last.isoformat()
    txn_first, txn_last = conn.execute(
        "SELECT MIN(date(txn_date)), MAX(date(txn_date)) FROM transactions"
    ).fetchone()
    p_start, p_end = conn.execute(
        "SELECT MIN(period_start), MAX(period_end) FROM statements"
    ).fetchone()
    ledger_start = min(d for d in (txn_first, p_start) if d)
    ledger_end = max(d for d in (txn_last, p_end) if d)

    if ledger_end > last_iso:
        covered = last_iso
    else:
        month_last_txn = conn.execute(
            "SELECT MAX(date(txn_date)) FROM transactions "
            "WHERE date(txn_date) BETWEEN ? AND ?",
            (first_iso, last_iso),
        ).fetchone()[0]
        period_ends = [
            r[0] for r in conn.execute(
                "SELECT MIN(period_end, ?) FROM statements "
                "WHERE period_end >= ? AND (period_start IS NULL OR period_start <= ?)",
                (last_iso, first_iso, last_iso),
            )
        ]
        covered = max([d for d in [month_last_txn, *period_ends] if d], default=first_iso)
    partial = covered < last_iso
    period = {
        "month": month,
        "start": first_iso,
        "end": last_iso,
        "covered_through": covered,
        "partial": partial,
    }

    prev = _prev_month(month)
    prev_first, prev_last = _month_bounds(prev)
    if partial:
        covered_day = date.fromisoformat(covered).day
        window_end = prev_first.replace(day=min(covered_day, prev_last.day))
    else:
        window_end = prev_last
    prev_has_data = conn.execute(
        "SELECT 1 FROM transactions WHERE date(txn_date) BETWEEN ? AND ? LIMIT 1",
        (prev_first.isoformat(), prev_last.isoformat()),
    ).fetchone()
    if not prev_has_data or ledger_start > prev_first.isoformat():
        return period, None
    return period, {
        "month": prev,
        "start": prev_first.isoformat(),
        "end": window_end.isoformat(),
    }


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


# Memo for _spend_offsets, keyed by database file. The computation walks the
# whole ledger (correctness needs the full set of settled credit ids for the
# people ledger), so it can't be scoped to the requested month; instead cache it
# and invalidate on a cheap fingerprint of everything that can change an offset.
# Dashboard + Money Map load it back-to-back for the same state, which this
# collapses to one walk. Keyed by db path so distinct databases (notably each
# in-memory test DB) never collide on a coincidentally-equal fingerprint.
_offsets_cache: dict = {}


def _db_identity(conn: sqlite3.Connection) -> str | None:
    """The main database's file path, or None for an in-memory DB (uncacheable:
    many short-lived in-memory DBs would share the None key and collide)."""
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return file or None
    return None


def _offsets_fingerprint(conn: sqlite3.Connection) -> tuple:
    """Signature of every input to _spend_offsets, sensitive to *any* row change.

    Two independent change counters make it complete:
    - conn.total_changes: SQLite's running count of rows inserted/updated/deleted
      through *this* connection. Catches same-connection edits - including a
      category re-label that leaves all row counts unchanged - which a
      count/timestamp fingerprint misses at datetime()'s one-second resolution.
    - data_version (PRAGMA): bumps when *another* connection commits, catching
      writes this connection didn't make.
    Together they change on every committed modification from any source."""
    data_version = conn.execute("PRAGMA data_version").fetchone()[0]
    return (conn.total_changes, data_version)


def _spend_offsets(conn: sqlite3.Connection) -> tuple[dict, set[str]]:
    """Compute spend offsets across the full ledger (memoized on a ledger fingerprint).

    Returns (category_offsets, used_credit_ids):
    - category_offsets: {(credit_date, category): amount} for offsets
      attributable to a spend category, landing on the day the credit arrived
      (sum them over any range with _offsets_in_range). A credit attributable to no category nets nothing;
      it still shows up in the verdict's cash view as money in.
    - used_credit_ids: credit transactions consumed as offsets, so other
      panels (the people ledger) can avoid counting a settled share twice.
    """
    db_id = _db_identity(conn)
    fingerprint = _offsets_fingerprint(conn)
    cached = _offsets_cache.get(db_id) if db_id is not None else None
    if cached is not None and cached[0] == fingerprint:
        cached_offsets, cached_ids = cached[1]
        # Hand back copies so callers can't mutate the memo.
        return dict(cached_offsets), set(cached_ids)
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
               date(ta.txn_date) AS a_date, aa.category AS a_category,
               tb.id AS b_id, tb.debit_credit AS b_dc, tb.amount AS b_amount,
               date(tb.txn_date) AS b_date, ab.category AS b_category
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
            category_offsets[(row[f"{credit}_date"], debit_category or "Uncategorized")] += amount

    # 2. Group credits: shares friends paid back inside an expense group,
    #    allocated pro rata across the group's spend debits.
    member_rows = conn.execute(
        """
        SELECT gm.group_id, gm.txn_type, t.id, t.amount, t.debit_credit,
               date(t.txn_date) AS credit_date, a.category
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
                category_offsets[(cred["credit_date"], deb["category"] or "Uncategorized")] += share

    # 3. Unlinked credits nobody grouped or linked. A credit annotated with a
    #    spend category nets that category directly - the label already
    #    asserts it is money coming back for spending. An unsorted credit
    #    (Uncategorized) asserts nothing, so it never offsets spend. Income > Refund
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
               date(t.txn_date) AS credit_date, a.category
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'credit'
          AND ((a.category = 'Income' AND a.subcategory = 'Refund')
               OR a.category NOT IN ({placeholders}))
          AND NOT {unsorted_sql("a")}
        """,
        NON_SPEND_CATEGORIES,
    ).fetchall()
    for row in credit_rows:
        if row["id"] in used_credit_ids:
            continue
        used_credit_ids.add(row["id"])
        if row["category"] != "Income":
            category_offsets[(row["credit_date"], row["category"])] += row["amount"]
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
                category_offsets[(row["credit_date"], match["category"] or "Uncategorized")] += row["amount"]
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
                    (row["credit_date"], amount_match["category"] or "Uncategorized")
                ] += take

    result = (dict(category_offsets), used_credit_ids)
    if db_id is not None:
        _offsets_cache[db_id] = (fingerprint, result)
    # Return copies so a caller mutating its result can't corrupt the memo.
    return dict(result[0]), set(result[1])


def _offsets_in_range(cat_offsets: dict, start: str, end: str) -> dict[str, float]:
    """Offsets whose credit arrived in [start, end], summed per category."""
    out: dict[str, float] = defaultdict(float)
    for (credit_date, category), amount in cat_offsets.items():
        if start <= credit_date <= end:
            out[category] += amount
    return out


def _verdict(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """Cash-view tiles for [start, end]; see the module docstring for the model.

    No linking, netting or heuristics feed these numbers, so a missed
    reimbursement can never make the tiles contradict each other - a friend's
    unmatched payback simply shows up inside In (and other_in).
    """
    excluded = ",".join("?" for _ in OUT_EXCLUDED_CATEGORIES)
    row = conn.execute(
        f"""
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
              AND COALESCE(a.category, '') NOT IN ({excluded})
              THEN t.amount END), 0) AS money_out,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit' AND a.category = 'Investments'
              THEN t.amount END), 0) AS invested
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE date(t.txn_date) BETWEEN ? AND ?
        """,
        (*OUT_EXCLUDED_CATEGORIES, start, end),
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


def _categories_for_range(conn: sqlite3.Connection, start: str, end: str, cat_offsets: dict) -> dict[str, dict]:
    """Net spend per category over [start, end], with a subcategory drill-down."""
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    rows = conn.execute(
        f"""
        SELECT COALESCE(a.category, 'Uncategorized') AS category,
               a.subcategory, COUNT(*) AS n, SUM(t.amount) AS total
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?
          AND COALESCE(a.category, 'Uncategorized') NOT IN ({placeholders})
        GROUP BY 1, 2
        """,
        (start, end, *NON_SPEND_CATEGORIES),
    ).fetchall()
    categories: dict[str, dict] = {}
    for row in rows:
        cat = categories.setdefault(
            row["category"],
            {"gross": 0.0, "count": 0, "subcategories": defaultdict(lambda: {"total": 0.0, "count": 0})},
        )
        cat["gross"] += row["total"]
        cat["count"] += row["n"]
        sub = cat["subcategories"][row["subcategory"] or "Other"]
        sub["total"] += row["total"]
        sub["count"] += row["n"]
    # Offsets can land in a window where the category had no debit (e.g. a
    # refund arriving a month later); surface those as negative-net rows.
    offsets = _offsets_in_range(cat_offsets, start, end)
    for category in offsets:
        if category not in categories:
            categories[category] = {
                "gross": 0.0, "count": 0,
                "subcategories": defaultdict(lambda: {"total": 0.0, "count": 0}),
            }
    for category, data in categories.items():
        # Round gross and net first and derive offsets from them, so the three
        # always add up to the cent and the rows can reconcile to Out exactly.
        gross = round(data["gross"], 2)
        net = round(data["gross"] - offsets.get(category, 0.0), 2)
        data["gross"] = gross
        data["net"] = net
        data["offsets"] = round(gross - net, 2)
        data["subcategories"] = sorted(
            (
                {"name": name, "total": round(s["total"], 2), "count": s["count"]}
                for name, s in data["subcategories"].items()
            ),
            key=lambda s: -s["total"],
        )
    return categories


def _out_breakdown(
    conn: sqlite3.Connection, start: str, end: str, categories: dict[str, dict], money_out: float,
) -> dict:
    """The rows that, listed after the category rows, make the breakdown sum to Out.

    Out is gross cash that left (minus Self Transfers and Investments); the
    category rows are net spend. So Out = sum(net) + offsets counted in the
    window + Transfers debits (people and ATM) + any other debit no category
    row covers (Income-labelled debits). Each extra appears only when non-zero.
    `total` is Out itself (the verdict's figure, not a sum of these rows), so
    a test and the UI can check the list closes on it.
    """
    other_names = sorted(set(NON_SPEND_CATEGORIES) - set(OUT_EXCLUDED_CATEGORIES) - {"Transfers"})

    def debits(names: list[str]) -> tuple[int, float]:
        marks = ",".join("?" for _ in names)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total
            FROM transactions t
            JOIN annotations a ON a.transaction_id = t.id
            WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?
              AND a.category IN ({marks})
            """,
            (start, end, *names),
        ).fetchone()
        return row["n"], round(row["total"], 2)

    extras = []
    count, total = debits(["Transfers"])
    if total:
        extras.append({"key": "transfers", "label": "Transfers and cash", "total": total, "count": count})
    paid_back = round(sum(c["offsets"] for c in categories.values()), 2)
    if paid_back:
        extras.append({"key": "paid_back", "label": "Refunds and paybacks taken off the rows above", "total": paid_back, "count": None})
    count, total = debits(other_names)
    if total:
        extras.append({"key": "other", "label": "Other money out", "total": total, "count": count})
    return {"total": money_out, "extras": extras}


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
               a.category
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

    # Labelled by payee, the way the Ledger names it: a rename of one payment
    # changes nothing here, a rename of the payee changes this label and not the row.
    labels = payee_labels(conn, sorted({key for key, _amount in units}))
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
        dates = [c["txn_date"] for c in charges]
        gaps = [
            (_days_between(dates[i - 1], dates[i]))
            for i in range(1, len(dates))
        ]
        last_date = dates[-1]
        items.append({
            "name": labels[key],
            # The grouping key, for the drill to the payments this row adds up.
            "keys": [key],
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


def _recurring_summary(items: list[dict]) -> dict:
    """The one monthly-committed figure: active items billed monthly."""
    monthly = [i for i in items if i["active"] and i["cadence"] == "monthly"]
    return {"monthly_total": sum(i["amount"] for i in monthly), "count": len(monthly)}


def _days_between(earlier: str, later: str) -> int:
    d1 = date.fromisoformat(earlier[:10])
    d2 = date.fromisoformat(later[:10])
    return (d2 - d1).days


def _people_ledger(conn: sqlite3.Connection, settled_ids: set[str], start: str, end: str) -> dict:
    """Net position per person over the Transfers dated in [start, end].

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
          AND date(t.txn_date) BETWEEN ? AND ?
        """,
        (start, end),
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


def _merchants(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Top merchants for [start, end] on canonical keys, with count and avg ticket."""
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    rows = conn.execute(
        f"""
        SELECT UPPER(COALESCE(t.counterparty_key, a.merchant)) AS key,
               a.merchant, t.amount, t.counterparty_key AS cpk
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?
          AND COALESCE(a.category, 'Uncategorized') NOT IN ({placeholders})
          AND COALESCE(t.counterparty_key, a.merchant) IS NOT NULL
        """,
        (start, end, *NON_SPEND_CATEGORIES),
    ).fetchall()
    canonical = _merge_merchant_keys([r["key"] for r in rows])
    labels = payee_labels(conn, sorted({r["cpk"] for r in rows if r["cpk"]}))
    agg: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0, "names": Counter(), "keys": set()})
    for row in rows:
        entry = agg[canonical[row["key"]]]
        entry["total"] += row["amount"]
        entry["count"] += 1
        entry["keys"].add(row["key"])
        # The payee's name as the Ledger shows it; a payment with no payee key
        # is grouped by its merchant, which is then its name.
        entry["names"][labels.get(row["cpk"]) or (row["merchant"] or row["key"]).strip()] += 1
    items = [
        {
            "name": min(entry["names"].items(), key=lambda kv: (-kv[1], kv[0]))[0],
            "keys": sorted(entry["keys"]),
            "total": round(entry["total"], 2),
            "count": entry["count"],
            "avg": round(entry["total"] / entry["count"], 2),
        }
        for entry in agg.values()
    ]
    items.sort(key=lambda i: -i["total"])
    return items[:8]


def _balance_series(conn: sqlite3.Connection, max_points: int = 240) -> dict:
    """Running balance over the full history for a single account, one point per
    day (last balance of the day), downsampled evenly if the history outgrows
    max_points.

    running_balance is a per-account chain: each statement continues its own
    account's balance. Interleaving two real accounts by date produces a sawtooth
    that represents neither, so the series is scoped to one account: the busiest,
    meaning the one with the most balance-carrying rows. Which statements belong
    to an account is decided once, at ingest (db/queries/accounts.py).

    Returns {"account": <label or None>, "series": [{date, balance}, ...]}.
    """
    busiest = conn.execute(
        """
        SELECT a.id AS id, a.name AS name, a.bank_name AS bank_name,
               a.account_ref AS account_ref, COUNT(*) AS n
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        JOIN accounts a ON a.id = s.account_id
        WHERE t.running_balance IS NOT NULL
        GROUP BY a.id
        ORDER BY n DESC, a.id
        LIMIT 1
        """
    ).fetchone()
    if busiest is None:
        return {"account": None, "series": []}

    rows = conn.execute(
        """
        SELECT t.txn_date AS date, t.running_balance AS balance
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        WHERE t.running_balance IS NOT NULL AND s.account_id = ?
        ORDER BY t.txn_date, t.rowid
        """,
        (busiest["id"],),
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

    label = account_label(busiest["name"], busiest["bank_name"], busiest["account_ref"])
    return {"account": label, "series": series}


def _unexplained(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """Debits in [start, end] that are not sorted (common.unsorted_sql)."""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?
          AND {unsorted_sql("a")}
        """,
        (start, end),
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
    months = payment_months(conn)
    if not months:
        return {"months": [], "month": None}
    if month is None or month not in months:
        month = months[-1]
    prev = _prev_month(month)
    period, comparison = _period_windows(conn, month)
    start, end = period["start"], period["end"]

    cat_offsets, settled_ids = _spend_offsets(conn)

    current = _categories_for_range(conn, start, end, cat_offsets)
    previous = (
        _categories_for_range(conn, comparison["start"], comparison["end"], cat_offsets)
        if comparison else None
    )

    categories = [
        {
            "category": name,
            "gross": data["gross"],
            "net": data["net"],
            "offsets": data["offsets"],
            "count": data["count"],
            "prev_net": previous.get(name, {}).get("net", 0.0) if previous is not None else None,
            "delta": (
                round(data["net"] - previous.get(name, {}).get("net", 0.0), 2)
                if previous is not None else None
            ),
            "subcategories": data["subcategories"],
        }
        for name, data in current.items()
    ]
    categories.sort(key=lambda c: -c["net"])

    deltas = []
    if previous is not None:
        # Uncategorized is never a shift worth naming: the unsorted figure and
        # its own breakdown row already cover it.
        for name in (set(current) | set(previous)) - {UNCATEGORIZED}:
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
    verdict = _verdict(conn, start, end)
    recurring = _recurring(conn, latest_date)

    return {
        "month": month,
        "prev_month": prev,
        "months": months,
        "period": period,
        "comparison": comparison,
        "verdict": {
            **verdict,
            "prev": _verdict(conn, comparison["start"], comparison["end"]) if comparison else None,
        },
        "what_changed": deltas[:3],
        "categories": categories,
        "breakdown": _out_breakdown(conn, start, end, current, verdict["money_out"]),
        "recurring": recurring,
        "recurring_summary": _recurring_summary(recurring),
        "people": _people_ledger(conn, settled_ids, start, end),
        "merchants": _merchants(conn, start, end),
        "balance": _balance_series(conn),
        "unexplained": _unexplained(conn, start, end),
        "annotation": _annotation_coverage(conn, month),
    }
