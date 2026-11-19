"""The downloadable Account List and Ledger templates load with their importers."""

from datetime import datetime
from io import BytesIO

import openpyxl

from engine import import_templates as t
from engine.member_loader import load_account_list
from engine.payment_resolver import load_ledger_from_excel


def _fill(content: bytes, sheet: str, row: list, path) -> str:
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb[sheet]
    for i, value in enumerate(row, start=1):
        ws.cell(row=2, column=i, value=value)
    wb.save(path)
    return str(path)


def _headers(content: bytes, sheet: str) -> list:
    wb = openpyxl.load_workbook(BytesIO(content))
    return [c.value for c in wb[sheet][1]]


def test_account_list_template_shape():
    content = t.account_list_template()
    wb = openpyxl.load_workbook(BytesIO(content))
    assert wb.sheetnames == [t.ACCOUNT_LIST_SHEET, t.NOTES_SHEET]
    assert "account" not in t.NOTES_SHEET.lower()        # the loader picks sheets containing "account"
    assert _headers(content, t.ACCOUNT_LIST_SHEET) == [c.header for c in t.ACCOUNT_LIST_COLUMNS]
    assert wb[t.ACCOUNT_LIST_SHEET].cell(row=2, column=7).number_format == "@"      # ZIP kept as text
    notes = [r[0] for r in wb[t.NOTES_SHEET].iter_rows(min_row=2, values_only=True)]
    assert notes[:len(t.ACCOUNT_LIST_COLUMNS)] == [c.header for c in t.ACCOUNT_LIST_COLUMNS]


def test_filled_account_list_template_loads(conn, tmp_path):
    examples = [c.example for c in t.ACCOUNT_LIST_COLUMNS]
    path = _fill(t.account_list_template(), t.ACCOUNT_LIST_SHEET, examples, tmp_path / "accounts.xlsx")
    assert load_account_list(conn, path) == (1, 0, 0)
    m = dict(conn.execute("SELECT * FROM member").fetchone())
    assert (m["name"], m["account_id"], m["premier_entity_code"], m["alliant_entity_code"], m["entity_code"],
            m["billing_city"], m["billing_state"], m["billing_zip"]) == (
        "Example General Hospital", "001Ab00000XyZ12AAA", "TX5012", "AL10234", "TX5012", "Austin", "TX", "02134")


def test_unedited_account_list_template_loads_nothing(conn, tmp_path):
    path = tmp_path / "blank.xlsx"
    path.write_bytes(t.account_list_template())
    assert load_account_list(conn, str(path)) == (0, 0, 0)


def test_filled_ledger_template_loads(conn, tmp_path):
    content = t.ledger_template()
    assert _headers(content, t.LEDGER_SHEET) == [c.header for c in t.LEDGER_COLUMNS]
    row = ["Medline", datetime(2026, 10, 15), 14527.87, "a1B5f00000AbCdE", "AL-DS-001"]
    path = _fill(content, t.LEDGER_SHEET, row, tmp_path / "ledger.xlsx")
    assert load_ledger_from_excel(conn, path) == 1
    r = dict(conn.execute("SELECT payor, payment_received_date, deposit_amount, sf_payment_id, contract_id "
                          "FROM payment_ledger").fetchone())
    assert r == {"payor": "Medline", "payment_received_date": "2026-10-15", "deposit_amount": 14527.87,
                 "sf_payment_id": "a1B5f00000AbCdE", "contract_id": "AL-DS-001"}

