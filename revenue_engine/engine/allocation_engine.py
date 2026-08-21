"""
Tier C allocation for the Revenue Processing Engine.

Given a set of extracted totals (from pivot_extractor.py) and a supplier's
supplier_allocation_rule records, computes how much money each pre-known
facility (member) receives. Pure function — no DB writes; the caller
(processor.py) persists results as revenue_row records.
"""

from dataclasses import dataclass
from typing import Optional

from .constants import ALLOC_PERCENT, ALLOC_FIXED_AMOUNT, ALLOC_REMAINDER


class AllocationError(Exception):
    """Raised when a total's allocation rules don't reconcile."""


@dataclass
class AllocationResult:
    extraction_rule_name: str
    member_id: int
    amount: float
    is_sales_amount: bool
    contract_number: Optional[str] = None
    product_line: Optional[str] = None


def load_allocation_rules(conn: object, supplier_profile_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM supplier_allocation_rule WHERE supplier_profile_id = ? AND is_active = 1",
        (supplier_profile_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def allocate(
    conn: object,
    supplier_profile_id: int,
    extracted_values: dict[str, float],
    is_sales_amount: dict[str, bool],
) -> tuple[list[AllocationResult], list[str]]:
    """
    Returns (results, warnings). Raises AllocationError only for a total
    that has *no* allocation rules configured at all — a total that IS
    configured but doesn't cleanly reconcile (percentages don't sum to
    100, fixed amounts exceed the total) is downgraded to a warning plus
    a best-effort split, so the job still lands in needs_review for a
    human to fix rather than silently failing.
    """
    rules = load_allocation_rules(conn, supplier_profile_id)
    if not rules:
        raise AllocationError(
            f"No supplier_allocation_rule records configured for supplier_profile_id={supplier_profile_id}"
        )

    rules_by_extraction: dict[str, list[dict]] = {}
    for r in rules:
        rules_by_extraction.setdefault(r["extraction_rule_name"], []).append(r)

    results: list[AllocationResult] = []
    warnings: list[str] = []

    for rule_name, total in extracted_values.items():
        rule_set = rules_by_extraction.get(rule_name)
        if not rule_set:
            warnings.append(
                f"Extracted total {rule_name!r} (${total:.2f}) has no allocation rules configured — skipped"
            )
            continue

        remainder_rules = [r for r in rule_set if r["allocation_type"] == ALLOC_REMAINDER]
        if len(remainder_rules) > 1:
            warnings.append(
                f"Extraction rule {rule_name!r} has more than one 'remainder' allocation row — using the first"
            )
            remainder_rules = remainder_rules[:1]

        fixed_and_percent = [r for r in rule_set if r["allocation_type"] != ALLOC_REMAINDER]
        allocated_so_far = 0.0
        rule_results: list[AllocationResult] = []

        for r in fixed_and_percent:
            if r["allocation_type"] == ALLOC_PERCENT:
                amount = total * (float(r["allocation_value"]) / 100.0)
            elif r["allocation_type"] == ALLOC_FIXED_AMOUNT:
                amount = float(r["allocation_value"])
            else:
                warnings.append(f"Unknown allocation_type {r['allocation_type']!r} on rule id={r['id']} — skipped")
                continue
            allocated_so_far += amount
            rule_results.append(
                AllocationResult(
                    extraction_rule_name=rule_name,
                    member_id=r["member_id"],
                    amount=amount,
                    is_sales_amount=is_sales_amount.get(rule_name, False),
                    contract_number=r.get("contract_number"),
                    product_line=r.get("product_line"),
                )
            )

        if remainder_rules:
            r = remainder_rules[0]
            amount = total - allocated_so_far
            allocated_so_far += amount
            rule_results.append(
                AllocationResult(
                    extraction_rule_name=rule_name,
                    member_id=r["member_id"],
                    amount=amount,
                    is_sales_amount=is_sales_amount.get(rule_name, False),
                    contract_number=r.get("contract_number"),
                    product_line=r.get("product_line"),
                )
            )

        diff = abs(allocated_so_far - total)
        if diff > 0.01:
            warnings.append(
                f"Extraction rule {rule_name!r}: allocated ${allocated_so_far:.2f} "
                f"does not reconcile to extracted total ${total:.2f} (diff ${diff:.2f}) — "
                f"check supplier_allocation_rule percentages/amounts"
            )

        results.extend(rule_results)

    return results, warnings
