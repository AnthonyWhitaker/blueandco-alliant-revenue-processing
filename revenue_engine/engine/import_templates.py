"""
Blank templates for the Account List and the payment ledger, so people know
which columns the importers read.

Each template has the data sheet first (empty apart from its headers, so an
unedited template loads nothing) and a "How to fill in" sheet describing
every column with an example. The headers are ones the importers recognise:
tests/test_import_templates.py loads a filled-in template with each importer.
"""

from dataclasses import dataclass
from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ACCOUNT_LIST_FILE_NAME = "Account List template.xlsx"
LEDGER_FILE_NAME = "Ledger template.xlsx"

# member_loader reads the first sheet whose name contains "account"; the notes sheet must not.
ACCOUNT_LIST_SHEET = "Account List"
# load_ledger_from_excel reads the sheet named in Admin > Payment Ledger, "Ledger" by default.
LEDGER_SHEET = "Ledger"
NOTES_SHEET = "How to fill in"


@dataclass(frozen=True)
class TemplateColumn:
    header: str
    required: str          # "Yes", "No", or a short qualifier
    description: str
    example: str
    text: bool = False     # format as text, so Excel keeps leading zeros and long IDs intact
    date: bool = False


ACCOUNT_LIST_COLUMNS = [
    TemplateColumn("Account Name", "Yes", "Facility name as it appears in Salesforce. Rows without one are skipped.",
                   "Example General Hospital"),
    TemplateColumn("Account ID", "Recommended",
                   "Salesforce Account ID (15 or 18 characters). Loading the list again updates members by this ID. "
                   "A row without one is recognised by Account Name + entity code + state instead, and isn't "
                   "deactivated by a full refresh.", "001Ab00000XyZ12AAA", text=True),
    TemplateColumn("Premier Entity Code", "One of the two codes",
                   "Premier entity code (PEC). Used as Facility ID in the upload file, and to match supplier "
                   "files that print an entity code or GPO ID.", "TX5012", text=True),
    TemplateColumn("Alliant Entity Code", "One of the two codes",
                   "Alliant entity code, used when there is no Premier code. Without either code the facility's "
                   "output row gets a placeholder PEC.", "AL10234", text=True),
    TemplateColumn("Billing City", "Recommended", "Used to match supplier files by name and location.", "Austin"),
    TemplateColumn("Billing State/Province", "Recommended", "Two-letter state code or the full name.", "TX"),
    TemplateColumn("Billing Zip/Postal Code", "Recommended",
                   "5-digit ZIP (ZIP+4 is fine). Formatted as text so leading zeros are kept.", "02134", text=True),
]

ACCOUNT_LIST_NOTES = [
    "One row per facility. A Salesforce Account report exported to Excel works as is if it has these columns.",
    "Other header spellings are also recognised: Name, Salesforce ID, Facility ID, PEC, City, State, Zip, "
    "Postal Code.",
    f"Only the sheet named '{ACCOUNT_LIST_SHEET}' (the first sheet whose name contains 'Account') is read.",
    "Load it in Admin > Members. Tick 'Replace all existing members' when the file is the complete list: "
    "members with an Account ID that are not in it are deactivated (never deleted).",
]

LEDGER_COLUMNS = [
    TemplateColumn("Payor", "Yes", "Who sent the payment. Matched against the supplier's payor name.", "Medline"),
    TemplateColumn("Payment Received Date", "Recommended", "Date the deposit arrived (mm/dd/yyyy).",
                   "10/15/2026", date=True),
    TemplateColumn("Deposit Amount", "Yes", "Deposit amount as a number. Supplier files are matched to "
                   "payments by this amount.", "14527.87"),
    TemplateColumn("SF Payment ID", "Yes", "Salesforce Payment ID written to the upload file.", "a1B5f00000AbCdE",
                   text=True),
    TemplateColumn("Contract ID", "No", "Contract number or Salesforce contract ID.", "AL-DS-001", text=True),
]

LEDGER_NOTES = [
    "One row per payment received. Rows without a payor, amount or SF Payment ID are skipped.",
    f"The sheet is read by name: '{LEDGER_SHEET}' unless another name is entered in Admin > Payment Ledger.",
    "Loading a ledger adds its payments; payments already loaded are not duplicated.",
]

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TEMPLATE_ROWS = 1000      # rows pre-formatted as text/date below the header


def _build(sheet: str, columns: list[TemplateColumn], notes: list[str]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for i, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i, value=col.header)
        cell.font, cell.fill = _HEADER_FONT, _HEADER_FILL
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = max(len(col.header), len(col.example)) + 4
        if col.text or col.date:
            fmt = "@" if col.text else "mm/dd/yyyy"
            for row in range(2, _TEMPLATE_ROWS + 2):
                ws.cell(row=row, column=i).number_format = fmt
    ws.freeze_panes = "A2"

    notes_ws = wb.create_sheet(NOTES_SHEET)
    notes_ws.append(["Column", "Required", "What goes here", "Example"])
    for cell in notes_ws[1]:
        cell.font, cell.fill = _HEADER_FONT, _HEADER_FILL
    for col in columns:
        notes_ws.append([col.header, col.required, col.description, col.example])
    notes_ws.append([])
    for note in notes:
        notes_ws.append([note])
    for letter, width in zip("ABCD", (26, 22, 90, 26)):
        notes_ws.column_dimensions[letter].width = width
    for row in notes_ws.iter_rows(min_row=2, max_col=3):
        row[2].alignment = Alignment(wrap_text=True, vertical="top")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def account_list_template() -> bytes:
    return _build(ACCOUNT_LIST_SHEET, ACCOUNT_LIST_COLUMNS, ACCOUNT_LIST_NOTES)


def ledger_template() -> bytes:
    return _build(LEDGER_SHEET, LEDGER_COLUMNS, LEDGER_NOTES)
