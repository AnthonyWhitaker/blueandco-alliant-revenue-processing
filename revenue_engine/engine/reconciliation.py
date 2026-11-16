"""
Per-run totals check: input = output + exceptions.

Input is measured from the source data as read (every data row of every file,
after summary/total rows are stripped), independently of what was stored.
Output is the Upload tab (revenue_output_row). Exceptions are the rows left
out of the output (unverified against the GPO key, or unmatched). The two
amounts checked are volume (sales_amount, e.g. Medline extended price) and
revenue (fee_amount, e.g. GPO fee). Volume is not checked for suppliers whose
Volume is left blank (volume_mode = 'blank').

Summary rows stripped by the reader are reported alongside but are not part of
the input: a grand-total row would otherwise count every amount twice.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from .constants import VOLUME_BLANK

TOLERANCE = 0.005   # half a cent


@dataclass
class InputTotals:
    rows: int
    volume: float
    revenue: float
    unparseable_volume: int = 0      # non-blank values that aren't numbers (counted as 0 everywhere)
    unparseable_revenue: int = 0
    dropped_rows: int = 0
    dropped_volume: float = 0.0
    dropped_revenue: float = 0.0
    rows_per_file: dict[str, int] = field(default_factory=dict)
    # [{source_file, source_row, volume, revenue}] for each summary row left out (first 1000)
    dropped_detail: list[dict] = field(default_factory=list)


@dataclass
class Reconciliation:
    input: InputTotals
    output_rows: int
    output_volume: Optional[float]
    output_revenue: float
    exception_rows: int
    exceptions_volume: float
    exceptions_revenue: float
    volume_checked: bool
    volume_difference: Optional[float]
    revenue_difference: float
    balanced: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    def summary(self) -> str:
        parts = [f"revenue in {self.input.revenue:,.2f} = out {self.output_revenue:,.2f} + "
                 f"exceptions {self.exceptions_revenue:,.2f} (diff {self.revenue_difference:,.2f})"]
        if self.volume_checked:
            parts.append(f"volume in {self.input.volume:,.2f} = out {self.output_volume or 0:,.2f} + "
                         f"exceptions {self.exceptions_volume:,.2f} (diff {self.volume_difference:,.2f})")
        else:
            parts.append("volume not checked (Volume is left blank for this supplier)")
        return "; ".join(parts)


def input_totals_from_json(raw: Optional[str]) -> Optional[InputTotals]:
    """The input totals stored with a job's last reconciliation, for re-checking after review decisions."""
    if not raw:
        return None
    data = json.loads(raw).get("input")
    return InputTotals(**data) if data else None


def input_totals_from_rows(conn: object, job_id: int) -> InputTotals:
    """Input totals from the stored rows (Tier C jobs, and jobs processed before reconciliation existed)."""
    r = conn.execute(
        "SELECT COUNT(*) AS n, SUM(sales_amount) AS vol, SUM(fee_amount) AS rev FROM revenue_row WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return InputTotals(rows=r["n"] or 0, volume=r["vol"] or 0.0, revenue=r["rev"] or 0.0)


def reconcile(conn: object, job_id: int, volume_mode: Optional[str], input_totals: InputTotals) -> Reconciliation:
    out = conn.execute(
        "SELECT COUNT(*) AS n, SUM(volume) AS vol, SUM(total_revenue) AS rev FROM revenue_output_row WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    exc = conn.execute(
        """
        SELECT COUNT(*) AS n, SUM(sales_amount) AS vol, SUM(fee_amount) AS rev
        FROM revenue_row
        WHERE job_id = ? AND match_status NOT IN ('matched', 'alias_matched')
        """,
        (job_id,),
    ).fetchone()
    output_volume = out["vol"]
    output_revenue = out["rev"] or 0.0
    exceptions_volume = exc["vol"] or 0.0
    exceptions_revenue = exc["rev"] or 0.0

    revenue_diff = round(input_totals.revenue - (output_revenue + exceptions_revenue), 2)
    volume_checked = volume_mode != VOLUME_BLANK
    volume_diff = (round(input_totals.volume - ((output_volume or 0.0) + exceptions_volume), 2)
                   if volume_checked else None)
    balanced = abs(revenue_diff) < TOLERANCE and (not volume_checked or abs(volume_diff) < TOLERANCE)
    return Reconciliation(
        input=input_totals,
        output_rows=out["n"] or 0,
        output_volume=output_volume,
        output_revenue=output_revenue,
        exception_rows=exc["n"] or 0,
        exceptions_volume=exceptions_volume,
        exceptions_revenue=exceptions_revenue,
        volume_checked=volume_checked,
        volume_difference=volume_diff,
        revenue_difference=revenue_diff,
        balanced=balanced,
    )
