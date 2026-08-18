"""
Payment ID resolution for the Revenue Processing Engine.

Resolves a Salesforce Payment ID for each job using:
  1. Manual override (user-provided)
  2. Auto-lookup: match deposit amount + payor in payment_ledger table
  3. Column-based: read Payment ID directly from a column in the raw file
"""

import sqlite3
from typing import Optional

from .normalizer import parse_deposit_amount_from_filename


def resolve_payment_id(
    conn: sqlite3.Connection,
    supplier_profile_id: int,
    filename: str,
    payor_filter: Optional[str] = None,
    manual_payment_id: Optional[str] = None,
    raw_df=None,
    payment_id_column: Optional[str] = None,
) -> tuple[Optional[str], Optional[float], str]:
    """
    Attempt to resolve the Salesforce Payment ID for a job.

    Returns:
        (payment_id, deposit_amount, source)
        source is one of: 'manual', 'ledger_auto', 'column', 'not_found'
    """
    # Priority 1: manual override
    if manual_payment_id:
        deposit = parse_deposit_amount_from_filename(filename)
        return manual_payment_id, deposit, "manual"

    # Priority 2: column in raw file
    if raw_df is not None and payment_id_column and payment_id_column in raw_df.columns:
        values = raw_df[payment_id_column].dropna().unique()
        if len(values) == 1:
            return str(values[0]), None, "column"
        elif len(values) > 1:
            # Multiple different payment IDs in one file — use first non-null
            return str(values[0]), None, "column"

    # Priority 3: auto-lookup from ledger
    deposit = parse_deposit_amount_from_filename(filename)
    if deposit is not None:
        result = lookup_payment_id_from_ledger(conn, deposit, payor_filter)
        if result:
            return result, deposit, "ledger_auto"

    return None, deposit, "not_found"


def lookup_payment_id_from_ledger(
    conn: sqlite3.Connection,
    deposit_amount: float,
    payor_filter: Optional[str] = None,
    tolerance: float = 0.02,
) -> Optional[str]:
    """
    Look up a Salesforce Payment ID from the payment_ledger table.
    Matches by deposit_amount (within tolerance) and optionally by payor name.
    If multiple rows match, returns the one with the most recent payment date.
    """
    params: list = [deposit_amount - tolerance, deposit_amount + tolerance]
    sql = """
        SELECT sf_payment_id, payment_received_date
        FROM payment_ledger
        WHERE deposit_amount BETWEEN ? AND ?
    """
    if payor_filter:
        sql += " AND lower(payor) LIKE lower(?)"
        params.append(f"%{payor_filter}%")
    sql += " ORDER BY payment_received_date DESC LIMIT 1"

    row = conn.execute(sql, params).fetchone()
    return row["sf_payment_id"] if row else None


def load_ledger_from_excel(
    conn: sqlite3.Connection,
    ledger_path: str,
    sheet_name: str = "Ledger",
) -> int:
    """
    Import Ledger.xlsx into the payment_ledger table.
    Returns the number of rows inserted.

    Expected columns (case-insensitive):
        Payor, Payment Received Date, Deposit Amount, SF Payment ID, Contract ID
    """
    import pandas as pd

    df = pd.read_excel(ledger_path, sheet_name=sheet_name)
    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    col_map = {
        "payor": "payor",
        "payment_received_date": "payment_received_date",
        "deposit_amount": "deposit_amount",
        "sf_payment_id": "sf_payment_id",
        "contract_id": "contract_id",
    }

    count = 0
    for _, row in df.iterrows():
        try:
            payor = str(row.get("payor", "")).strip()
            amount_raw = row.get("deposit_amount")
            sf_id = str(row.get("sf_payment_id", "")).strip()
            if not payor or not sf_id or amount_raw is None:
                continue
            try:
                amount = float(amount_raw)
            except (ValueError, TypeError):
                continue

            date_raw = row.get("payment_received_date")
            try:
                date_val = pd.to_datetime(date_raw).strftime("%Y-%m-%d") if date_raw else None
            except Exception:
                date_val = None

            contract = str(row.get("contract_id", "")).strip() or None

            conn.execute(
                """
                INSERT OR IGNORE INTO payment_ledger
                    (payor, payment_received_date, deposit_amount, sf_payment_id, contract_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (payor, date_val, amount, sf_id, contract),
            )
            count += 1
        except Exception:
            continue

    conn.commit()
    return count
