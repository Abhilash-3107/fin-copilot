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
- "Not sorted" has one definition (common.unsorted_sql): a payment with no
  annotation, whose category is NULL or Uncategorized, or that could be one side
  of a transfer between the user's own accounts which pairing was not sure
  enough to make. Every figure here that groups by category reads the effective
  category (common.effective_category_sql: Uncategorized when not sorted, else
  the label), so the unexplained figure, the Uncategorized breakdown row and the
  Transactions filter are the same rows, and In, Out, Invested and Kept still
  add up with them.
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
- Everything is computed over date windows, not a month string, so a month
  still in progress is compared like for like. A month is covered to a different
  day in each account, so each account is compared over the same days of the
  previous month as it is covered to this month, and an account that cannot be
  compared (a new one, or one with a gap in the previous month) is left out of both
  sides, never counted as zero (see _period_windows).

Additivity across accounts (summarize_insights with account_id): the figures of the
accounts add up to the figures for all accounts for
- the verdict tiles (In, Out, Invested, Kept, Earned), and their previous month and
  the same-days figure the deltas are measured against (`verdict.prev` and
  `verdict.compared`);
- each category's gross, net, offsets (an offset counts in the account of the debit it
  pays back), previous net and delta, for every category that has spending this month
  in the accounts that list it; the unsorted figure and the sorting coverage; and the
  people ledger, each person's sent, received, net and count, and the unmatched payments.
They do not add up for
- recurring charges: a charge is recurring when it repeats across months at one
  merchant, which is judged over the payments in view, so two accounts' halves of a
  pattern may each fall short and the whole may qualify (or the reverse);
- top merchants: ranked and cut to eight in each view;
- what changed: the top three shifts of each view;
- the balance line: always one account's, never a sum;
- the period itself: a month is complete only when every counted account is.
"""
from __future__ import annotations

import calendar
import sqlite3
import zlib
from collections import Counter, defaultdict
from datetime import date
from statistics import median

from finance_copilot.db.queries.accounts import account_label, account_labels
from finance_copilot.db.queries.common import (
    ALL_ACCOUNTS,
    UNCATEGORIZED,
    account_scope_sql,
    effective_category_sql,
    effective_subcategory_sql,
    unsorted_sql,
    waiting_on_pair_sql,
    windows_condition_sql,
)
from finance_copilot.db.queries.coverage import covered_ranges, covered_through, statement_spans
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


def _period_windows(
    conn: sqlite3.Connection, month: str, account_id: str | None = None
) -> tuple[dict, dict | None, dict | None]:
    """How far the ledger covers `month`, and the like-for-like windows to compare with.

    Returns (period, comparison, windows).

    period = {month, start, end, covered_through, partial, counted, short}: start/end are
    the calendar bounds of the month, covered_through is the earliest day any counted
    account is covered to. A month is complete only when every account that has a
    statement touching it is covered to the month's last day (coverage.py: the printed
    period when a statement has one, else its payments, run on to the month's end when the
    account's next statement follows; a hole inside the month ends the coverage there).
    An account with no statement touching the month does not hold it back (the books grid
    shows it as a gap). With `account_id`, only that account counts. `short` lists the
    accounts covered to less than the whole month, each with the day it is covered to
    ({account_id, account_label, covered_through}), so the page can say which one is
    behind; `counted` is how many accounts the month was judged by.

    The comparison is cut account by account, because a month is covered to a different
    day in each: an account covered to day N is compared with days 1..N of the previous
    month (the whole of it when the account is covered to the month's last day, and never
    past the previous month's last day). An account whose statements do not cover that
    window from its first day, or that has no payment at all in the previous month, is
    left out: a gap, a new account or a quiet one is not a month of zero, and must not
    show as a change. `windows` = {"current": [...], "previous": [...]} holds one
    (account_id, first_day, last_day) for each account that is compared, on this month and
    on the previous one; every figure on either side of a delta is summed over them
    (common.windows_condition_sql), so both sides are over the same accounts and days, and
    All accounts is exactly the sum of its accounts.

    comparison = {month, start, end, uniform}: the previous month, `end` the latest window
    end, and `uniform` false when the accounts' windows end on different days (the page
    then says "same days for each account"). comparison and windows are None when no
    account can be compared (the first month of a ledger has nothing to compare with).

    The caller decides, from the clock, whether a partial month is really in progress.
    The comparison over covered days is like for like either way.
    """
    first, last = _month_bounds(month)
    first_iso, last_iso = first.isoformat(), last.isoformat()
    spans = statement_spans(conn, account_id)
    covered = {
        acct: through for acct, items in spans.items()
        if (through := covered_through(items, first_iso, last_iso)) is not None
    }
    through = min(covered.values()) if covered else last_iso
    partial = through < last_iso
    labels = account_labels(conn)
    period = {
        "month": month,
        "start": first_iso,
        "end": last_iso,
        "covered_through": through,
        "partial": partial,
        # How many accounts the month was judged by (those with a statement touching it).
        "counted": len(covered),
        "short": sorted(
            (
                {
                    "account_id": acct,
                    "account_label": labels.get(acct, "Other statements"),
                    "covered_through": day,
                }
                for acct, day in covered.items() if day < last_iso
            ),
            key=lambda item: (item["covered_through"], item["account_label"]),
        ),
    }

    prev_first, prev_last = _month_bounds(_prev_month(month))
    prev_first_iso = prev_first.isoformat()
    paid = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT s.account_id FROM transactions t JOIN statements s ON s.id = t.statement_id "
            "WHERE date(t.txn_date) BETWEEN ? AND ?",
            (prev_first_iso, prev_last.isoformat()),
        )
    }
    current, previous = [], []
    for acct, day in sorted(covered.items(), key=lambda item: (item[0] is None, item[0] or "")):
        if day >= last_iso:
            window_end = prev_last
        else:
            window_end = prev_first.replace(day=min(date.fromisoformat(day).day, prev_last.day))
        window = (prev_first_iso, window_end.isoformat())
        if acct in paid and covered_ranges(spans[acct], *window) == [window]:
            current.append((acct, first_iso, day))
            previous.append((acct, *window))
    if not previous:
        return period, None, None
    ends = {end for _acct, _start, end in previous}
    comparison = {
        "month": _prev_month(month),
        "start": prev_first_iso,
        "end": max(ends),
        "uniform": len(ends) == 1,
    }
    return period, comparison, {"current": current, "previous": previous}


def _is_spend(category: str | None) -> bool:
    """Whether a debit in this category can be paid back as spending. Uncategorized is not
    one: nothing says what an unsorted payment was, so a refund is never taken off it. The
    Uncategorized row therefore always shows the unsorted figure, whatever refunds arrive."""
    category = category or UNCATEGORIZED
    return category not in NON_SPEND_CATEGORIES and category != UNCATEGORIZED


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
# and invalidate on a fingerprint of everything that can change an offset. On the 20,000-row
# synthetic ledger the walk takes about 350 ms and the fingerprint about 30 ms, so the memo
# earns its place: Dashboard + Money Map load back-to-back for the same state, which this
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


# Everything _spend_offsets reads, as (rowid, the columns that decide an offset). Annotations
# carry the category and source (the effective category also reads whether a payment waits on
# a pair, which depends on the payments, so those are here too), statements carry the account.
_OFFSET_INPUTS = (
    "SELECT rowid, statement_id, txn_date, amount, debit_credit, counterparty_key FROM transactions",
    "SELECT rowid, transaction_id, category, subcategory, source FROM annotations",
    "SELECT rowid, txn_a, txn_b, link_type FROM transaction_links",
    "SELECT rowid, group_id, transaction_id, txn_type FROM transaction_group_members",
    "SELECT rowid, account_id FROM statements",
)


def _offsets_fingerprint(conn: sqlite3.Connection) -> tuple:
    """Signature of every input to _spend_offsets, sensitive to any change visible to this
    connection, whichever connection made it.

    Each request opens its own connection, and a fresh connection always reports
    total_changes 0 and data_version 1, so neither counter can tell one request's ledger
    from the next. The signature is therefore read from the data: a checksum of every row
    that decides an offset (a category re-label that leaves every count unchanged moves it).
    It reads those rows once, which costs a few percent of the walk it saves."""
    checksum = 0
    for query in _OFFSET_INPUTS:
        for row in conn.execute(query):
            checksum = zlib.crc32(repr(tuple(row)).encode(), checksum)
    return (checksum,)


def _spend_offsets(conn: sqlite3.Connection) -> tuple[dict, set[str]]:
    """Compute spend offsets across the full ledger (memoized on a ledger fingerprint).

    Which credit nets which debit is decided over the whole ledger, whatever account is
    being looked at: a refund in one account can pay back a purchase in another, and a
    debit's capacity is used up once. An offset is counted in the account of the debit it
    pays back, on the day the credit arrived, in that debit's category: for a link, for each
    debit's share of a group credit, and for a counterparty or amount match. A view of one
    account therefore nets, in each category, the refunds of its own purchases whichever
    account they arrived in, and never shows a negative row for a refund of something it did
    not buy; all accounts together net every credit once, so the accounts' figures add up to
    the figures for all. The credit itself still counts in the In of the account it arrived
    in. Only a credit with a spend category and no debit behind it stays with the credit's
    account. An unsorted debit is never offset (_is_spend). Each view's breakdown closes on
    its Out, because its rows and its "refunds and paybacks" line use the same offsets.

    Returns (category_offsets, used_credit_ids):
    - category_offsets: {(credit_date, category, debit_account_id): amount} for offsets
      attributable to a spend category, landing on the day the credit arrived
      (sum them over any window with _offsets_in_windows). A credit attributable to no
      category nets nothing; it still shows up in the verdict's cash view as money in.
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
    category_offsets: dict[tuple[str, str, str | None], float] = defaultdict(float)
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
        f"""
        SELECT l.link_type,
               ta.id AS a_id, ta.debit_credit AS a_dc, ta.amount AS a_amount,
               date(ta.txn_date) AS a_date, {effective_category_sql("aa", "ta")} AS a_category,
               (SELECT account_id FROM statements WHERE id = ta.statement_id) AS a_account,
               tb.id AS b_id, tb.debit_credit AS b_dc, tb.amount AS b_amount,
               date(tb.txn_date) AS b_date, {effective_category_sql("ab", "tb")} AS b_category,
               (SELECT account_id FROM statements WHERE id = tb.statement_id) AS b_account
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
            category_offsets[
                (row[f"{credit}_date"], debit_category, row[f"{debit}_account"])
            ] += amount

    # 2. Group credits: shares friends paid back inside an expense group,
    #    allocated pro rata across the group's spend debits.
    member_rows = conn.execute(
        f"""
        SELECT gm.group_id, gm.txn_type, t.id, t.amount, t.debit_credit,
               date(t.txn_date) AS credit_date, {effective_category_sql("a")} AS category,
               (SELECT account_id FROM statements WHERE id = t.statement_id) AS account_id
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
                category_offsets[
                    (cred["credit_date"], deb["category"], deb["account_id"])
                ] += share

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
               date(t.txn_date) AS credit_date, a.category,
               (SELECT account_id FROM statements WHERE id = t.statement_id) AS account_id
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
            category_offsets[(row["credit_date"], row["category"], row["account_id"])] += row["amount"]
            continue
        if row["counterparty_key"]:
            match = conn.execute(
                f"""
                SELECT {effective_category_sql("a")} AS category,
                       (SELECT account_id FROM statements WHERE id = t.statement_id) AS account_id
                FROM transactions t
                JOIN annotations a ON a.transaction_id = t.id
                WHERE t.debit_credit = 'debit' AND t.counterparty_key = ?
                  AND t.txn_date <= ?
                ORDER BY t.txn_date DESC LIMIT 1
                """,
                (row["counterparty_key"], row["txn_date"]),
            ).fetchone()
            if match and _is_spend(match["category"]):
                category_offsets[(row["credit_date"], match["category"], match["account_id"])] += row["amount"]
                continue
        amount_match = conn.execute(
            f"""
            SELECT t.id, t.amount, {effective_category_sql("a")} AS category,
                   (SELECT account_id FROM statements WHERE id = t.statement_id) AS account_id
            FROM transactions t
            LEFT JOIN annotations a ON a.transaction_id = t.id
            WHERE t.debit_credit = 'debit' AND t.amount = ?
              AND t.txn_date <= ? AND t.txn_date >= date(?, '-60 days')
              AND {effective_category_sql("a")} NOT IN ({placeholders}, '{UNCATEGORIZED}')
            ORDER BY t.txn_date DESC LIMIT 1
            """,
            (row["amount"], row["txn_date"], row["txn_date"], *NON_SPEND_CATEGORIES),
        ).fetchone()
        if amount_match:
            take = _consume(amount_match["id"], amount_match["amount"], row["amount"])
            if take > 0:
                category_offsets[(row["credit_date"], amount_match["category"], amount_match["account_id"])] += take

    result = (dict(category_offsets), used_credit_ids)
    if db_id is not None:
        _offsets_cache[db_id] = (fingerprint, result)
    # Return copies so a caller mutating its result can't corrupt the memo.
    return dict(result[0]), set(result[1])


def _offsets_in_windows(cat_offsets: dict, windows: list[tuple[str | None, str, str]]) -> dict[str, float]:
    """Offsets that count in the windows (common.windows_condition_sql's), summed per category.
    An offset counts in a window when its credit arrived in the window's days and it is
    taken off a debit of the window's account."""
    out: dict[str, float] = defaultdict(float)
    for (credit_date, category, debit_account), amount in cat_offsets.items():
        for window_account, start, end in windows:
            if start <= credit_date <= end and window_account in (ALL_ACCOUNTS, debit_account):
                out[category] += amount
                break
    return out


def _verdict(conn: sqlite3.Connection, windows: list[tuple[str | None, str, str]]) -> dict:
    """Cash-view tiles over the payments in `windows` (common.windows_condition_sql: one
    month of one account or of all, or the per-account windows of a comparison); see the
    module docstring for the model.

    No linking, netting or heuristics feed these numbers, so a missed
    reimbursement can never make the tiles contradict each other - a friend's
    unmatched payback simply shows up inside In (and other_in).
    """
    excluded = ",".join("?" for _ in OUT_EXCLUDED_CATEGORIES)
    condition, condition_params = windows_condition_sql(windows)
    row = conn.execute(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.debit_credit = 'credit'
              AND cat != 'Self Transfers'
              AND NOT (cat = 'Income' AND COALESCE(sub, '') = 'Opening Balance')
              THEN t.amount END), 0) AS money_in,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'credit' AND cat = 'Income'
              AND COALESCE(sub, '') NOT IN ('Refund', 'Opening Balance')
              THEN t.amount END), 0) AS earned,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit'
              AND cat NOT IN ({excluded})
              THEN t.amount END), 0) AS money_out,
          COALESCE(SUM(CASE WHEN t.debit_credit = 'debit' AND cat = 'Investments'
              THEN t.amount END), 0) AS invested
        FROM (
          SELECT t.debit_credit, t.amount,
                 {effective_category_sql("a")} AS cat,
                 {effective_subcategory_sql("a")} AS sub
          FROM transactions t
          LEFT JOIN annotations a ON a.transaction_id = t.id
          WHERE {condition}
        ) t
        """,
        (*OUT_EXCLUDED_CATEGORIES, *condition_params),
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


def _categories_for_range(
    conn: sqlite3.Connection, windows: list[tuple[str | None, str, str]], cat_offsets: dict
) -> dict[str, dict]:
    """Net spend per category over the payments in `windows`, with a subcategory drill-down."""
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    condition, condition_params = windows_condition_sql(windows)
    rows = conn.execute(
        f"""
        SELECT category, subcategory, COUNT(*) AS n, SUM(amount) AS total
        FROM (
          SELECT {effective_category_sql("a")} AS category,
                 {effective_subcategory_sql("a")} AS subcategory, t.amount
          FROM transactions t
          LEFT JOIN annotations a ON a.transaction_id = t.id
          WHERE t.debit_credit = 'debit' AND {condition}
        )
        WHERE category NOT IN ({placeholders})
        GROUP BY 1, 2
        """,
        (*condition_params, *NON_SPEND_CATEGORIES),
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
    offsets = _offsets_in_windows(cat_offsets, windows)
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
    account_id: str | None = None,
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

    scope, scope_params = account_scope_sql(account_id)

    def debits(names: list[str]) -> tuple[int, float]:
        marks = ",".join("?" for _ in names)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total
            FROM transactions t
            LEFT JOIN annotations a ON a.transaction_id = t.id
            WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?{scope}
              AND {effective_category_sql("a")} IN ({marks})
            """,
            (start, end, *scope_params, *names),
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


def _recurring(conn: sqlite3.Connection, latest_date: str, account_id: str | None = None) -> list[dict]:
    """Detect committed money: charges of the same amount from the same
    counterparty across months (SIPs, subscriptions, rent-like transfers).

    A (counterparty, rounded amount) unit is recurring when it shows up in 3+
    months covering at least 60% of its span, or in 2+ months for categories
    that are commitments by definition (Subscriptions, Investments). Units
    charged more than ~1.5 times per active month are habitual spending at a
    fixed price point (canteens, metro fares), not commitments, and are dropped.
    With an account, only its payments are looked at and `latest_date` is its newest.
    """
    scope, scope_params = account_scope_sql(account_id)
    rows = conn.execute(
        f"""
        SELECT key, amount, txn_date, month, category
        FROM (
          SELECT t.counterparty_key AS key, CAST(ROUND(t.amount) AS INTEGER) AS amount,
                 t.txn_date, strftime('%Y-%m', t.txn_date) AS month,
                 CASE WHEN {waiting_on_pair_sql("a")} THEN '{UNCATEGORIZED}' ELSE a.category END AS category
          FROM transactions t
          LEFT JOIN annotations a ON a.transaction_id = t.id
          WHERE t.debit_credit = 'debit' AND t.counterparty_key IS NOT NULL{scope}
        )
        WHERE COALESCE(category, '') NOT IN ('Transfers', 'Self Transfers')
        ORDER BY txn_date
        """,
        scope_params,
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


def _people_ledger(
    conn: sqlite3.Connection, settled_ids: set[str], start: str, end: str, account_id: str | None = None
) -> dict:
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
    scope, scope_params = account_scope_sql(account_id)
    rows = conn.execute(
        f"""
        SELECT t.id, t.amount, t.debit_credit, t.txn_date,
               LOWER(COALESCE(t.counterparty_key, '')) AS cpk,
               LOWER(COALESCE(a.merchant, '')) AS merchant
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        WHERE {effective_category_sql("a")} = 'Transfers'
          AND COALESCE({effective_subcategory_sql("a")}, '') != 'ATM Withdrawal'
          AND date(t.txn_date) BETWEEN ? AND ?{scope}
        """,
        (start, end, *scope_params),
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


def _merchants(conn: sqlite3.Connection, start: str, end: str, account_id: str | None = None) -> list[dict]:
    """Top merchants for [start, end] on canonical keys, with count and avg ticket."""
    placeholders = ",".join("?" for _ in NON_SPEND_CATEGORIES)
    scope, scope_params = account_scope_sql(account_id)
    rows = conn.execute(
        f"""
        SELECT UPPER(COALESCE(t.counterparty_key, a.merchant)) AS key,
               a.merchant, t.amount, t.counterparty_key AS cpk
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?{scope}
          AND {effective_category_sql("a")} NOT IN ({placeholders})
          AND COALESCE(t.counterparty_key, a.merchant) IS NOT NULL
        """,
        (start, end, *scope_params, *NON_SPEND_CATEGORIES),
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


def _balance_series(conn: sqlite3.Connection, max_points: int = 240, account_id: str | None = None) -> dict:
    """Running balance over the full history for a single account, one point per
    day (last balance of the day), downsampled evenly if the history outgrows
    max_points.

    running_balance is a per-account chain: each statement continues its own
    account's balance. Interleaving two real accounts by date produces a sawtooth
    that represents neither, and a sum of accounts would be a line nobody holds, so the
    series is always one account's: the selected one, or with all accounts the busiest,
    meaning the one with the most balance-carrying rows. Which statements belong
    to an account is decided once, at ingest (db/queries/accounts.py).

    Returns {"account": <label or None>, "series": [{date, balance}, ...]}. A selected
    account with no balances answers with its label and no points, so the page can say
    which account has none.
    """
    if account_id:
        chosen = conn.execute(
            "SELECT id, name, bank_name, account_ref FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    else:
        chosen = conn.execute(
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
    if chosen is None:
        return {"account": None, "series": []}
    label = account_label(chosen["name"], chosen["bank_name"], chosen["account_ref"])

    rows = conn.execute(
        """
        SELECT t.txn_date AS date, t.running_balance AS balance
        FROM transactions t
        JOIN statements s ON s.id = t.statement_id
        WHERE t.running_balance IS NOT NULL AND s.account_id = ?
        ORDER BY t.txn_date, t.rowid
        """,
        (chosen["id"],),
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

    return {"account": label, "series": series}


def _unexplained(conn: sqlite3.Connection, start: str, end: str, account_id: str | None = None) -> dict:
    """Debits in [start, end] that are not sorted (common.unsorted_sql)."""
    scope, scope_params = account_scope_sql(account_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE t.debit_credit = 'debit' AND date(t.txn_date) BETWEEN ? AND ?{scope}
          AND {unsorted_sql("a")}
        """,
        (start, end, *scope_params),
    ).fetchone()
    return {"count": row["n"], "total": round(row["total"], 2)}


def _annotation_coverage(conn: sqlite3.Connection, month: str, account_id: str | None = None) -> dict:
    """How much of the month the pipeline has touched. annotated == 0 with
    transactions present means auto-annotation never ran for this month; the
    dashboard uses that to hold back insights until the user starts it."""
    scope, scope_params = account_scope_sql(account_id)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT t.id) AS total,
               COUNT(DISTINCT a.transaction_id) AS annotated
        FROM transactions t
        LEFT JOIN annotations a ON a.transaction_id = t.id
        WHERE strftime('%Y-%m', t.txn_date) = ?{scope}
        """,
        (month, *scope_params),
    ).fetchone()
    return {"total": row["total"], "annotated": row["annotated"]}


def summarize_insights(
    conn: sqlite3.Connection, month: str | None = None, account_id: str | None = None
) -> dict:
    """Every figure of the Dashboard and Money Map for one month, over all accounts or
    over one account's payments (`account_id`). One set of definitions either way, so
    the identities (the breakdown sums to Out, Kept = In - Out - Invested, the recurring
    header equals its rows, a drill-through totals the figure it was clicked from) hold
    for each account and for all. `months` is always the ledger's: the period is shared,
    and an account with no payments in a month reads as an empty month, not a missing one."""
    months = payment_months(conn)
    if not months:
        return {"months": [], "month": None}
    if month is None or month not in months:
        month = months[-1]
    prev = _prev_month(month)
    period, comparison, windows = _period_windows(conn, month, account_id)
    start, end = period["start"], period["end"]
    whole = [(account_id or ALL_ACCOUNTS, start, end)]

    cat_offsets, settled_ids = _spend_offsets(conn)

    current = _categories_for_range(conn, whole, cat_offsets)
    # What the deltas measure: this month over the accounts and days the comparison covers
    # (all of the month, in the usual case where every account is covered to its end), against
    # the same accounts' days of the previous month.
    if windows is None:
        compared, previous = None, None
    else:
        # Every counted account compared over the whole month: the month itself.
        whole_month = len(windows["current"]) == period["counted"] and all(
            (first, last) == (start, end) for _acct, first, last in windows["current"]
        )
        compared = current if whole_month else _categories_for_range(conn, windows["current"], cat_offsets)
        previous = _categories_for_range(conn, windows["previous"], cat_offsets)

    categories = [
        {
            "category": name,
            "gross": data["gross"],
            "net": data["net"],
            "offsets": data["offsets"],
            "count": data["count"],
            "prev_net": previous.get(name, {}).get("net", 0.0) if previous is not None else None,
            "delta": (
                round(compared.get(name, {}).get("net", 0.0) - previous.get(name, {}).get("net", 0.0), 2)
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
        for name in (set(compared) | set(previous)) - {UNCATEGORIZED}:
            cur_net = compared.get(name, {}).get("net", 0.0)
            prev_net = previous.get(name, {}).get("net", 0.0)
            delta = round(cur_net - prev_net, 2)
            if abs(delta) >= 1:
                deltas.append({
                    "category": name, "delta": delta,
                    "current": cur_net, "previous": prev_net,
                })
        deltas.sort(key=lambda d: -abs(d["delta"]))

    scope, scope_params = account_scope_sql(account_id)
    latest_date = conn.execute(
        f"SELECT MAX(t.txn_date) FROM transactions t WHERE 1 = 1{scope}", scope_params
    ).fetchone()[0]
    verdict = _verdict(conn, whole)
    recurring = _recurring(conn, latest_date, account_id) if latest_date else []

    return {
        "month": month,
        "prev_month": prev,
        "months": months,
        "account": _account_ref(conn, account_id),
        "period": period,
        "comparison": comparison,
        "verdict": {
            **verdict,
            # The comparison's two sides over the same accounts and days: `compared` is this
            # month over them (the tile itself when every account is compared in full), `prev`
            # the previous month's. A tile's delta is compared - prev.
            "compared": _verdict(conn, windows["current"]) if windows else None,
            "prev": _verdict(conn, windows["previous"]) if windows else None,
        },
        "what_changed": deltas[:3],
        "categories": categories,
        "breakdown": _out_breakdown(conn, start, end, current, verdict["money_out"], account_id),
        "recurring": recurring,
        "recurring_summary": _recurring_summary(recurring),
        "people": _people_ledger(conn, settled_ids, start, end, account_id),
        "merchants": _merchants(conn, start, end, account_id),
        "balance": _balance_series(conn, account_id=account_id),
        "unexplained": _unexplained(conn, start, end, account_id),
        "annotation": _annotation_coverage(conn, month, account_id),
    }


def _account_ref(conn: sqlite3.Connection, account_id: str | None) -> dict | None:
    """{id, label} of the account the figures are for, None for all accounts."""
    if not account_id:
        return None
    label = account_labels(conn).get(account_id)
    return {"id": account_id, "label": label} if label else None
