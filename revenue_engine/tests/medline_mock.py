"""
Small Medline-shaped mock data for the GPO key, rollup and multi-file tests.
Invented for tests only. Members come from tests/fixtures.make_members:
EC001 General Hospital, EC002 Mercy Medical Center, EC003 Desert Regional Health.
"""

import csv
from pathlib import Path

import openpyxl

HEADERS = [
    "InvoiceNumber", "InvoiceDate", "Medline Account #", "Account Name", "Account Street",
    "Account City", "Account State", "Account Zip", "GPO ID", "Extended Price Amount",
    "GPO Fee Rate", "GPO Fee Amount",
]

# The key: account, name, street, correct GPO ID, ZIP. Two Medline accounts belong to EC001.
KEY_ROWS = [
    ("1001", "GENERAL HOSPITAL", "1 MAIN ST", "EC001", "78701"),
    ("1002", "GENERAL HOSPITAL - DOCK", "1 MAIN ST", "EC001", "78701"),
    ("2001", "MERCY MEDICAL CENTER", "200 ELM ST", "EC002", "78701"),
    ("3001", "DESERT REGIONAL HEALTH", "9 SAND RD", "EC003", "78701"),
]


def line(inv, day, acct, name, street, gpo, ext, fee):
    return [inv, day, acct, name, street, "AUSTIN", "TX", "78701", gpo, ext, 0.03, fee]


# Verified: 1001 x3 (two in Sep, one in Oct), 1002 (blank GPO ID in file), 2001 (wrong GPO ID, plus a credit).
# Exceptions: 3001 address differs, 4001 not in key, a line with no account, 2001 with a different name.
ROWS = [
    line("INV1", "2026-09-03", "1001", "General Hospital", "1 Main St", "EC001", 100.00, 3.00),
    line("INV2", "2026-09-20", "1001", "GENERAL HOSPITAL", "1 MAIN ST.", "EC001", 50.00, 1.50),
    line("INV3", "2026-09-10", "1002", "GENERAL HOSPITAL - DOCK", "1 MAIN ST", None, 200.00, 6.00),
    line("INV4", "2026-10-02", "1001", "GENERAL HOSPITAL", "1 MAIN ST", "EC001", 10.00, 0.30),
    line("INV5", "2026-09-05", "2001", "MERCY MEDICAL CENTER", "200 ELM ST", "EC999", 300.00, 9.00),
    line("INV6", "2026-09-06", "2001", "MERCY MEDICAL CENTER", "200 ELM ST", "EC999", -40.00, -1.20),
    line("INV7", "2026-09-07", "3001", "DESERT REGIONAL HEALTH", "9 SAND ROAD", "EC003", 70.00, 2.10),
    line("INV8", "2026-09-08", "4001", "UNKNOWN CLINIC", "5 NOWHERE LN", "EC001", 25.00, 0.75),
    line("INV9", "2026-09-09", None, "GENERAL HOSPITAL", "1 MAIN ST", "EC001", 15.00, 0.45),
    line("INV10", "2026-09-11", "2001", "MERCY MED CTR", "200 ELM ST", "EC002", 30.00, 0.90),
]
INPUT_VOLUME = 760.00
INPUT_REVENUE = 22.80
EXCEPTION_REASONS = {
    "INV7": "address_mismatch",
    "INV8": "account_not_in_key",
    "INV9": "missing_account_number",
    "INV10": "name_mismatch",
}
EXCEPTIONS_VOLUME = 140.00
EXCEPTIONS_REVENUE = 4.20
# (GPO ID, earned month) -> (volume, revenue)
EXPECTED_UPLOAD = {
    ("EC001", "2026-09-01"): (350.00, 10.50),
    ("EC001", "2026-10-01"): (10.00, 0.30),
    ("EC002", "2026-09-01"): (260.00, 7.80),
}


KEY_HEADERS = ("Medline Account Number", "Medline Account Name", "Medline Street", "Final GPO ID", "Medline Zip")


def write_key_csv(path: Path, rows=KEY_ROWS, headers=KEY_HEADERS) -> Path:
    """Rows are (account, name, street, GPO ID[, ZIP]); a missing ZIP is 78701. Only len(headers) columns are written."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows([(tuple(r) + ("78701",))[:len(headers)] if len(r) == 4 else tuple(r)[:len(headers)]
                     for r in rows])
    return path


def write_medline_xlsx(path: Path, rows=ROWS, grand_total: bool = True) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(list(r))
    if grand_total:
        # Like the real file: a total row with no invoice number.
        ws.append([None] * 8 + ["Grand Total", sum(r[9] for r in rows), None, sum(r[11] for r in rows)])
    wb.save(path)
    return path
