"""Shape the review queue as questions, not rows."""
from __future__ import annotations

import math

from finance_copilot.parsers.description import parse_description
from finance_copilot.parsers.payee import classify_payee


def _impact(item: dict) -> float:
    """Uncertainty x size: a wrong label on a large payment costs more."""
    return (1.0 - item["confidence"]) * math.log1p(abs(item["amount"]))


def group_review_queue(items: list[dict]) -> list[dict]:
    """Group queued items by payee and direction (payments and refunds to one
    payee are different questions), adjacent and oldest first within a group.

    Groups rank by their largest item's impact plus ln(group size): a payee
    seen many times rises, but never above a single large unknown just by
    count. Adds group_key, group_size, payee and payee_kind to every item.
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        key = (
            f"{item['counterparty_key']}|{item['debit_credit']}"
            if item["counterparty_key"] else f"txn:{item['transaction_id']}"
        )
        groups.setdefault(key, []).append(item)

    ordered: list[dict] = []
    for key, members in sorted(
        groups.items(), key=lambda kv: -(max(map(_impact, kv[1])) + math.log(len(kv[1])))
    ):
        payee = parse_description(members[0]["raw_description"]).counterparty
        kind = classify_payee(payee)
        for item in sorted(members, key=lambda i: (i["txn_date"], i["annotation_id"])):
            ordered.append({**item, "group_key": key, "group_size": len(members), "payee": payee, "payee_kind": kind})
    return ordered
