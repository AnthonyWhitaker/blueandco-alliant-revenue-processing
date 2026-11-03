"""Suppliers, members, ledger, column maps, aliases, dashboard, meta."""

import openpyxl
import pytest
from pydantic import ValidationError

from core import column_maps, members, misc, suppliers
from core.errors import ConflictError, InvalidInputError, NotFoundError
from engine import constants
from models.column_maps import ColumnMappingInput, InferredMappingInput
from models.enums import AllocationType, CanonicalField, ExtractionMethod, JobType
from models.members import MemberInput
from models.misc import AliasInput
from models.suppliers import SupplierInput

from .conftest import make_members, make_supplier


def test_enums_match_engine_constants():
    from typing import get_args
    assert set(get_args(CanonicalField)) == set(misc.CANONICAL_FIELDS)
    assert set(get_args(JobType)) == {constants.JOB_TYPE_ROW_LEVEL, constants.JOB_TYPE_PIVOT_ALLOCATION}
    assert set(get_args(ExtractionMethod)) == {
        v for k, v in vars(constants).items() if k.startswith("EXTRACT_")}
    assert set(get_args(AllocationType)) == {v for k, v in vars(constants).items() if k.startswith("ALLOC_")}


# --- suppliers -------------------------------------------------------------

def test_supplier_create_update_and_conflicts(conn):
    s = suppliers.create_supplier(conn, SupplierInput(name="Viscot", header_row=1, fee_rate=0.02))
    assert (s.header_row, s.fee_rate, s.job_type, s.requires_manual_confirmation) == (1, 0.02, "row_level", False)
    with pytest.raises(ConflictError):
        suppliers.create_supplier(conn, SupplierInput(name="Viscot"))
    other = suppliers.create_supplier(conn, SupplierInput(name="Zier"))
    with pytest.raises(ConflictError):
        suppliers.update_supplier(conn, other.id, SupplierInput(name="Viscot"))
    updated = suppliers.update_supplier(conn, s.id, SupplierInput(name="Viscot", job_type="pivot_allocation"))
    assert updated.job_type == "pivot_allocation"
    with pytest.raises(NotFoundError):
        suppliers.get_supplier(conn, 999)


def test_list_suppliers_by_job_type(conn):
    make_supplier(conn, name="A")
    make_supplier(conn, name="B", job_type="pivot_allocation")
    assert [s.name for s in suppliers.list_suppliers(conn, job_type="pivot_allocation")] == ["B"]


# --- members & ledger --------------------------------------------------------

def test_member_search_and_create(conn):
    make_members(conn)
    page = members.search_members(conn, term="mercy")
    assert page.total == 1 and page.items[0].entity_code == "EC002"
    assert members.search_members(conn, term="ec00").total == 3
    created = members.create_member(conn, MemberInput(name="New Clinic", account_id="SF-NEW", state="TX"))
    assert created.entity_code is None
    with pytest.raises(ConflictError):
        members.create_member(conn, MemberInput(name="Dup", account_id="SF-NEW"))
    with pytest.raises(ValidationError):
        MemberInput(name="  ")


def test_member_search_active_only(conn):
    ids = make_members(conn)
    conn.execute("UPDATE member SET is_active = 0 WHERE id = ?", (ids["mercy"],))
    conn.commit()
    assert members.search_members(conn, term="mercy").total == 0
    assert members.search_members(conn, term="mercy", active_only=False).total == 1


def test_import_account_list(conn, tmp_path):
    path = tmp_path / "Account List.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Accounts"
    ws.append(["Account ID", "Account Name", "Premier Entity Code", "Billing City", "Billing State/Province",
               "Billing Zip/Postal Code"])
    ws.append(["SF-1", "Lakeside Hospital", "EC100", "Austin", "TX", "78701"])
    wb.save(path)
    result = members.import_account_list(conn, str(path), replace_all=False)
    assert result.inserted == 1
    assert members.search_members(conn, term="Lakeside").items[0].entity_code == "EC100"


def _account_list(path, *rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Accounts"
    ws.append(["Account ID", "Account Name", "Premier Entity Code", "Billing City", "Billing State/Province"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return str(path)


def test_full_refresh_deactivates_instead_of_deleting(conn, processed_job, tmp_path):
    """FINDINGS F23: replace_all used to DELETE every member, which fails once any job refers to one."""
    ids = processed_job["members"]
    manual = members.create_member(conn, MemberInput(name="Walk-in Clinic"))  # no account_id
    alias_count = conn.execute("SELECT COUNT(*) FROM member_alias").fetchone()[0]

    result = members.import_account_list(conn, _account_list(
        tmp_path / "a.xlsx", ("SF-GEN", "General Hospital", "EC001", "Austin", "TX"),
        ("SF-NEW", "New Regional", "EC009", "Waco", "TX"),
    ), replace_all=True)
    assert (result.inserted, result.deactivated) == (2, 2)

    active = {m.name: m.id for m in members.search_members(conn, limit=100).items}
    assert active == {"General Hospital": ids["general"], "New Regional": active["New Regional"],
                      "Walk-in Clinic": manual.id}
    assert members.get_member(conn, ids["mercy"]).is_active is False
    assert conn.execute("SELECT COUNT(*) FROM member_alias").fetchone()[0] == alias_count

    # Back in the next Account List: reactivated, same id.
    members.import_account_list(conn, _account_list(
        tmp_path / "b.xlsx", ("SF-MER", "Mercy Medical Center", "EC002", "Dallas", "TX"),
    ), replace_all=False)
    assert members.get_member(conn, ids["mercy"]).is_active is True


def test_import_ledger_and_list(conn, tmp_path):
    path = tmp_path / "Ledger.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws.append(["Payor", "Payment Received Date", "Deposit Amount", "SF Payment ID", "Contract ID"])
    ws.append(["Acme Supply", "2025-07-02", 300.0, "PAY-9", "CON-1"])
    wb.save(path)
    assert members.import_ledger(conn, str(path), "Ledger").inserted == 1
    page = members.list_ledger_entries(conn)
    assert page.total == 1 and page.items[0].sf_payment_id == "PAY-9"
    with pytest.raises(InvalidInputError):
        members.import_ledger(conn, str(path), "NoSuchSheet")


# --- column maps -------------------------------------------------------------

def test_column_mapping_save_replaces_by_source(conn):
    sid = make_supplier(conn)
    column_maps.save_mapping(conn, sid, ColumnMappingInput(source_column="Fee", canonical_field="fee_amount"))
    m = column_maps.save_mapping(conn, sid, ColumnMappingInput(
        source_column="Fee", canonical_field="sales_amount", is_required=True))
    assert (m.canonical_field, m.is_required) == ("sales_amount", True)
    assert len(column_maps.list_mappings(conn, sid)) == 1
    # Pattern rules and identity hints are accepted (the legacy form offered neither).
    column_maps.save_mapping(conn, sid, ColumnMappingInput(
        source_column="contains:Total Revenue", canonical_field="fee_amount"))
    column_maps.save_mapping(conn, sid, ColumnMappingInput(source_column="GPO ID", canonical_field="entity_code_hint"))
    assert len(column_maps.list_mappings(conn, sid)) == 3


def test_infer_and_save_inferred(conn, tmp_path):
    sid = make_supplier(conn)
    path = tmp_path / "sample.csv"
    path.write_text("Customer Name,City,State,Admin Fee\nGeneral Hospital,Austin,TX,10\n")
    suggestions = column_maps.infer_from_sample(conn, sid, str(path))
    by_src = {s.source_column: s.canonical_field for s in suggestions}
    assert by_src["Customer Name"] == "customer_name"
    assert by_src["Admin Fee"] == "fee_amount"
    saved = column_maps.save_inferred_mappings(
        conn, sid, [InferredMappingInput(**s.model_dump()) for s in suggestions])
    assert len(saved) == len(suggestions) and not any(m.is_required for m in saved)


def test_infer_rejects_unreadable_file(conn, tmp_path):
    sid = make_supplier(conn)
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a workbook")
    with pytest.raises(InvalidInputError):
        column_maps.infer_from_sample(conn, sid, str(bad))


# --- aliases, dashboard, meta -----------------------------------------------

def test_alias_create_and_duplicate(conn):
    ids = make_members(conn)
    sid = make_supplier(conn)
    alias = misc.create_alias(conn, AliasInput(member_id=ids["general"], raw_name="GEN HOSP LLC"))
    assert (alias.member_name, alias.supplier_profile_id, alias.approved_by) == ("General Hospital", None, "admin")
    with pytest.raises(ConflictError):
        misc.create_alias(conn, AliasInput(member_id=ids["mercy"], raw_name="gen hosp llc"))
    scoped = misc.create_alias(conn, AliasInput(member_id=ids["general"], raw_name="GEN HOSP LLC",
                                                supplier_profile_id=sid))
    assert scoped.supplier_name == "Acme Supply"
    assert len(misc.list_aliases(conn)) == 2


def test_dashboard_stats(conn, processed_job):
    stats = misc.get_dashboard_stats(conn)
    assert (stats.active_members, stats.suppliers, stats.jobs, stats.pending_reviews) == (3, 1, 1, 1)


def test_meta_lists_every_canonical_field():
    assert "entity_code_hint" in misc.get_meta().canonical_fields
