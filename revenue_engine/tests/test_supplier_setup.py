"""Medline setup: scripts/load_gpo_key.py options, seed_presets() safety, and the startup check for empty keys."""

import importlib.util
import logging
from pathlib import Path

import openpyxl
from fastapi.testclient import TestClient

from api.main import create_app
from engine.gpo_key import load_gpo_key, suppliers_without_key
from engine.supplier_presets import MEDLINE, apply_preset, seed_presets

from .medline_mock import KEY_HEADERS, KEY_ROWS, MEDLINE_GPO_KEY, write_key_csv

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_SPEC = importlib.util.spec_from_file_location(
    "load_gpo_key_script", Path(__file__).parent.parent / "scripts" / "load_gpo_key.py")
load_gpo_key_script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(load_gpo_key_script)


def identity_mode(conn, name="Medline") -> str:
    return conn.execute("SELECT identity_mode FROM supplier_profile WHERE name = ?", (name,)).fetchone()[0]


def key_entries(conn, sid) -> int:
    return conn.execute("SELECT COUNT(*) FROM gpo_key_entry WHERE supplier_profile_id = ?", (sid,)).fetchone()[0]


def write_key_xlsx(path: Path, with_zip: bool) -> Path:
    """A summary sheet first, then the key on sheet 'Key' with a title row above the headers (header row 1)."""
    headers = KEY_HEADERS if with_zip else KEY_HEADERS[:4]
    wb = openpyxl.Workbook()
    wb.active.title = "Summary"
    wb.active.append(["Not the key"])
    ws = wb.create_sheet("Key")
    ws.append(["Medline -> Alliant account key"])
    ws.append(list(headers))
    for r in KEY_ROWS:
        ws.append(list(r)[:len(headers)])
    wb.save(path)
    return path


def script_columns(*argv):
    return load_gpo_key_script.key_columns(load_gpo_key_script.parse_args(["--supplier", "Medline", *argv]))


# --- scripts/load_gpo_key.py ---------------------------------------------------

def test_load_script_sheet_and_header_row_reach_their_own_fields(conn, tmp_path):
    path = write_key_xlsx(tmp_path / "key.xlsx", with_zip=True)
    columns = script_columns("--file", str(path), "--sheet", "Key", "--header-row", "1")
    assert (columns.sheet_name, columns.header_row, columns.zip) == ("Key", 1, "Medline Zip")
    sid = apply_preset(conn, MEDLINE)
    assert load_gpo_key(conn, sid, str(path), columns).loaded == len(KEY_ROWS)


def test_load_script_zip_column(tmp_path):
    with_zip = str(write_key_csv(tmp_path / "zip.csv"))
    without_zip = str(write_key_csv(tmp_path / "nozip.csv", headers=KEY_HEADERS[:4]))
    assert script_columns("--file", with_zip).zip == "Medline Zip"
    assert script_columns("--file", without_zip).zip is None              # default: used only when present
    assert script_columns("--file", with_zip, "--zip-column", "").zip is None
    assert script_columns("--file", without_zip, "--zip-column", "Zip5").zip == "Zip5"   # explicit: kept


def test_api_key_upload_sheet_and_header_row_reach_their_own_fields(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))
    sid = apply_preset(conn, MEDLINE)
    path = write_key_xlsx(tmp_path / "key.xlsx", with_zip=True)
    with TestClient(create_app()) as client:
        resp = client.put(f"/api/suppliers/{sid}/gpo-key", data={"sheet_name": "Key", "header_row": "1"},
                          files={"file": ("key.xlsx", path.read_bytes(), XLSX)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["loaded"] == len(KEY_ROWS)
    # Uploads through the API still compare the street only: no ZIP is stored even when the file has one.
    assert conn.execute("SELECT COUNT(*) FROM gpo_key_entry WHERE zip IS NOT NULL").fetchone()[0] == 0


# --- seed_presets ---------------------------------------------------------------

def test_seed_medline_runs_member_match_and_loads_a_key_without_zip(conn, tmp_path):
    result = seed_presets(conn, [MEDLINE], str(write_key_csv(tmp_path / "k.csv", headers=KEY_HEADERS[:4])))
    assert result.key_error is None and result.key_without_zip
    assert result.key_loaded == key_entries(conn, result.supplier_ids["Medline"]) == len(KEY_ROWS)
    assert identity_mode(conn) == "member_match"


def test_seed_switches_to_gpo_key_only_once_the_key_loads(conn, tmp_path):
    result = seed_presets(conn, [MEDLINE_GPO_KEY], str(write_key_csv(tmp_path / "k.csv")))
    assert result.held_back == [] and identity_mode(conn) == "gpo_key"


def test_seed_keeps_previous_mode_when_the_key_fails(conn, tmp_path):
    bad = write_key_csv(tmp_path / "bad.csv", headers=("Account", "Name", "Street", "GPO"))
    result = seed_presets(conn, [MEDLINE_GPO_KEY], str(bad))
    assert result.key_error and "missing configured column" in result.key_error
    assert result.held_back == ["Medline"]
    assert identity_mode(conn) == "member_match"
    assert key_entries(conn, result.supplier_ids["Medline"]) == 0


def test_seed_keeps_previous_mode_without_a_key_file(conn):
    result = seed_presets(conn, [MEDLINE_GPO_KEY])
    assert result.held_back == ["Medline"] and identity_mode(conn) == "member_match"


# --- startup check --------------------------------------------------------------

def test_startup_warns_about_gpo_key_mode_with_empty_key(conn, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))
    sid = apply_preset(conn, MEDLINE_GPO_KEY)       # applied directly: no key
    assert suppliers_without_key(conn) == ["Medline"]
    with caplog.at_level(logging.WARNING, logger="api"), TestClient(create_app()):
        pass
    assert any("'Medline' is in gpo_key mode but has no GPO key" in r.getMessage() for r in caplog.records)

    load_gpo_key(conn, sid, str(write_key_csv(tmp_path / "k.csv")))
    assert suppliers_without_key(conn) == []
