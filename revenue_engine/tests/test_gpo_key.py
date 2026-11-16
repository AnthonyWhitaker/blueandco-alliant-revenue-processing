"""GPO key loading and verification (engine/gpo_key.py), with mock key files."""

import openpyxl
import pytest

from engine.gpo_key import (
    GpoKey, KeyColumns, KeyFileError, VerifyOptions, load_gpo_key, normalize_account, normalize_text, normalize_zip,
    read_gpo_key,
)

from .conftest import make_supplier
from .medline_mock import KEY_HEADERS, KEY_ROWS, write_key_csv


@pytest.fixture
def key(tmp_path) -> GpoKey:
    return GpoKey.from_frame(read_gpo_key(str(write_key_csv(tmp_path / "key.csv"))))


def test_normalization():
    assert normalize_account("0001001") == normalize_account("1001.0") == normalize_account(1001) == "1001"
    assert normalize_account(" ab-12 ") == "AB-12"
    assert normalize_account(None) == normalize_account(float("nan")) == ""
    assert normalize_text("St. Mary's Hospital & Clinic") == "ST MARY S HOSPITAL AND CLINIC"
    assert normalize_text("  1 Main   St. ") == "1 MAIN ST"


def test_read_key_with_configured_column_names_csv_and_xlsx(tmp_path):
    headers = ("Acct No", "Customer", "Street Address", "Correct GPO")
    cols = KeyColumns(account_number="ACCT NO", account_name="customer", address="Street Address",
                      gpo_id=" Correct  GPO ", zip=None)
    df = read_gpo_key(str(write_key_csv(tmp_path / "k.csv", headers=headers)), cols)
    assert list(df["gpo_id"]) == [r[3] for r in KEY_ROWS]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Key"
    ws.append(["Medline GPO key, exported 2026-11-01"])  # a title row above the headers
    ws.append(list(headers))
    for r in KEY_ROWS:
        ws.append([int(r[0]), r[1], r[2], r[3]])  # account numbers stored as numbers
    wb.save(tmp_path / "k.xlsx")
    df = read_gpo_key(str(tmp_path / "k.xlsx"), KeyColumns(*headers, zip=None, sheet_name="Key", header_row=1))
    assert list(df["account_number_norm"]) == ["1001", "1002", "2001", "3001"]


def test_real_key_layout_finds_the_key_sheet(tmp_path):
    """The Medline Account Key workbook: a summary sheet first, then the Key sheet with ~26 columns."""
    headers = ["Premier Entity Code", "Medline Account Number", "Medline Account Name", "Medline Street",
               "Medline City", "Medline State", "Medline Zip", "Premier Address ID", "GPO ID (Medline)",
               "Alliant Entity Code", "Final GPO ID", "Final GPO ID Source", "SF Account ID (18)",
               "SF Account ID (15)", "SF Account Name", "SF Billing Street"]
    wb = openpyxl.Workbook()
    wb.active.title = "Summary"
    wb.active.append(["Metric", "Before (v1)", "After (v2)"])
    ws = wb.create_sheet("Key")
    ws.append(headers)
    ws.append(["600854", 1001, "GENERAL HOSPITAL", "1 MAIN ST", "AUSTIN", "TX", "78701", "", "OLD1",
               "", "600854", "Medline GPO ID", "001A", "001A", "General Hospital", "1 Main St"])
    ws.append(["", 1002, "NO ID CLINIC", "2 OAK AVE", "AUSTIN", "TX", "78702", "", "",
               "", "", "None - unresolved", "", "", "", ""])
    wb.save(tmp_path / "key.xlsx")

    key = GpoKey.from_frame(read_gpo_key(str(tmp_path / "key.xlsx")))   # default columns, no sheet name
    assert key.verify("1001", "General Hospital", "1 Main St.", "78701").gpo_id == "600854"   # Final GPO ID, not Medline's
    assert key.verify("1002", "NO ID CLINIC", "2 OAK AVE", "78702").reason == "key_entry_has_no_gpo_id"


def test_read_key_reports_missing_columns(tmp_path):
    path = write_key_csv(tmp_path / "k.csv")
    with pytest.raises(KeyFileError, match=r"gpo_id \('Correct GPO'\).*Columns in the file"):
        read_gpo_key(str(path), KeyColumns(gpo_id="Correct GPO"))
    with pytest.raises(KeyFileError, match="Unsupported"):
        read_gpo_key(str(tmp_path / "k.pdf"))


def test_load_replaces_the_key_and_skips_rows_without_account(conn, tmp_path):
    sid = make_supplier(conn, name="Medline")
    rows = KEY_ROWS + [("", "NO ACCOUNT", "1 X ST", "EC009"), ("1001", "GENERAL HOSPITAL", "1 MAIN ST", "EC001")]
    result = load_gpo_key(conn, sid, str(write_key_csv(tmp_path / "k.csv", rows)))
    assert (result.loaded, result.skipped_blank_account, result.accounts_with_several_entries) == (5, 1, 1)
    load_gpo_key(conn, sid, str(write_key_csv(tmp_path / "k2.csv", KEY_ROWS[:1])))
    assert conn.execute("SELECT COUNT(*) FROM gpo_key_entry WHERE supplier_profile_id = ?", (sid,)).fetchone()[0] == 1


def test_verified_row_takes_the_keys_gpo_id(key):
    v = key.verify("1001", "General Hospital", "1 Main St.", "78701")
    assert v.verified and v.gpo_id == "EC001"
    assert key.verify("0001002.0", "general hospital - dock", "1 MAIN ST", "78701-1234").gpo_id == "EC001"
    assert key.verify("1001", "GENERAL HOSPITAL", "1 MAIN ST", 78701.0).verified


def test_zip_normalization():
    assert normalize_zip("78701-1234") == normalize_zip(78701.0) == normalize_zip(" 78701 ") == "78701"
    assert normalize_zip(2134) == normalize_zip("02134") == "02134"     # Excel drops the leading zero
    assert normalize_zip(None) == normalize_zip("") == normalize_zip("N/A") == ""


def test_street_only_when_the_key_has_no_zip_column(tmp_path):
    path = write_key_csv(tmp_path / "k.csv", headers=KEY_HEADERS[:4])
    key = GpoKey.from_frame(read_gpo_key(str(path), KeyColumns(zip=None)))
    assert not key.match_zip
    assert key.verify("1001", "GENERAL HOSPITAL", "1 MAIN ST").gpo_id == "EC001"


@pytest.mark.parametrize("account, name, address, reason", [
    (None, "GENERAL HOSPITAL", "1 MAIN ST", "missing_account_number"),
    ("  ", "GENERAL HOSPITAL", "1 MAIN ST", "missing_account_number"),
    ("9999", "GENERAL HOSPITAL", "1 MAIN ST", "account_not_in_key"),
    ("1001", "GENERAL HOSP", "1 MAIN ST", "name_mismatch"),
    ("1001", "GENERAL HOSPITAL", "1 MAIN STREET", "address_mismatch"),
    ("1001", "OTHER PLACE", "2 OTHER ST", "name_and_address_mismatch"),
    ("1001", "", "1 MAIN ST", "name_mismatch"),          # a blank name never verifies
    ("1001", "GENERAL HOSPITAL", None, "address_mismatch"),
])
def test_unverified_rows_get_a_reason_and_no_gpo_id(key, account, name, address, reason):
    v = key.verify(account, name, address, "78701")
    assert (v.verified, v.gpo_id, v.reason) == (False, None, reason)
    assert v.detail


@pytest.mark.parametrize("zip_code", ["78799", None, ""])
def test_zip_must_match_too(key, zip_code):
    v = key.verify("1001", "GENERAL HOSPITAL", "1 MAIN ST", zip_code)
    assert (v.verified, v.reason) == (False, "address_mismatch")
    assert "78701" in v.detail


def test_ambiguous_and_blank_gpo_id_entries(tmp_path):
    rows = [("1001", "A HOSPITAL", "1 MAIN ST", "EC001"), ("1001", "A HOSPITAL", "1 MAIN ST", "EC002"),
            ("2001", "B HOSPITAL", "2 MAIN ST", ""),
            ("3001", "C HOSPITAL", "3 MAIN ST", "EC003"), ("3001", "C HOSPITAL", "3 MAIN ST", "EC003")]
    key = GpoKey.from_frame(read_gpo_key(str(write_key_csv(tmp_path / "k.csv", rows))))
    assert key.verify("1001", "A HOSPITAL", "1 MAIN ST", "78701").reason == "ambiguous_key_entries"
    assert key.verify("2001", "B HOSPITAL", "2 MAIN ST", "78701").reason == "key_entry_has_no_gpo_id"
    assert key.verify("3001", "C HOSPITAL", "3 MAIN ST", "78701").gpo_id == "EC003"  # duplicates that agree are fine


def test_tolerance_is_off_by_default_and_configurable(tmp_path):
    df = read_gpo_key(str(write_key_csv(tmp_path / "k.csv")))
    assert GpoKey.from_frame(df).verify("1001", "GENERAL HOSPITL", "1 MAIN ST", "78701").reason == "name_mismatch"
    lenient = GpoKey.from_frame(df, VerifyOptions(name_min_score=90, address_min_score=100))
    assert lenient.verify("1001", "GENERAL HOSPITL", "1 MAIN ST", "78701").gpo_id == "EC001"
