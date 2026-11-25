"""Supplier profiles and their per-supplier configuration: column maps and Tier C rules."""

import os
from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status

from core import column_maps, gpo_key, suppliers, tier_c
from engine.gpo_key import KeyColumns
from models.column_maps import ColumnMapping, ColumnMappingInput, InferredMapping, InferredMappingInput
from models.common import Notice
from models.enums import JobType
from models.suppliers import GpoKeyImportResult, GpoKeySummary, Supplier, SupplierInput
from models.tier_c import (
    AllocationRule, AllocationRuleDraft, AllocationRuleInput,
    ExtractionRule, ExtractionRuleInput, ExtractionRuleSaveResult,
)

from ..deps import Conn, upload_to_temp_file
from ..errors import PROBLEM_RESPONSES, problems

router = APIRouter(prefix="/suppliers", tags=["suppliers"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=list[Supplier])
def list_suppliers(conn: Conn, job_type: Optional[JobType] = None) -> list[Supplier]:
    return suppliers.list_suppliers(conn, job_type=job_type)


@router.post("", response_model=Supplier, responses=problems(409), status_code=status.HTTP_201_CREATED)
def create_supplier(conn: Conn, body: SupplierInput) -> Supplier:
    return suppliers.create_supplier(conn, body)


@router.get("/{supplier_id}", responses=problems(404), response_model=Supplier)
def get_supplier(conn: Conn, supplier_id: int) -> Supplier:
    return suppliers.get_supplier(conn, supplier_id)


@router.put("/{supplier_id}", responses=problems(404, 409), response_model=Supplier)
def update_supplier(conn: Conn, supplier_id: int, body: SupplierInput) -> Supplier:
    return suppliers.update_supplier(conn, supplier_id, body)


# --- GPO key -----------------------------------------------------------------

_KEY_DEFAULTS = KeyColumns()


@router.get("/{supplier_id}/gpo-key", responses=problems(404), response_model=GpoKeySummary, tags=["gpo key"],
            summary="The GPO key loaded for a supplier")
def get_gpo_key(conn: Conn, supplier_id: int) -> GpoKeySummary:
    return gpo_key.get_summary(conn, supplier_id)


@router.put("/{supplier_id}/gpo-key", responses=problems(404, 413), response_model=GpoKeyImportResult,
            tags=["gpo key"], summary="Replace a supplier's GPO key from a CSV/XLSX key file",
            description="The key lists, per supplier account, the account name, address and correct GPO ID. "
                        "Column names default to the placeholders in engine/gpo_key.KeyColumns.")
def put_gpo_key(
    request: Request,
    conn: Conn,
    supplier_id: int,
    file: Annotated[UploadFile, File(description="Key file (.csv, .xlsx, .xls, .xlsm)")],
    account_number_column: Annotated[str, Form()] = _KEY_DEFAULTS.account_number,
    account_name_column: Annotated[str, Form()] = _KEY_DEFAULTS.account_name,
    address_column: Annotated[str, Form()] = _KEY_DEFAULTS.address,
    gpo_id_column: Annotated[str, Form()] = _KEY_DEFAULTS.gpo_id,
    sheet_name: Annotated[Optional[str], Form()] = None,
    header_row: Annotated[int, Form(ge=0)] = 0,
) -> GpoKeyImportResult:
    path = upload_to_temp_file(request, file)
    try:
        # zip=None: uploads here compare the street only (ZIP comparison for UI uploads not decided yet).
        columns = KeyColumns(
            account_number=account_number_column,
            account_name=account_name_column,
            address=address_column,
            gpo_id=gpo_id_column,
            zip=None,
            sheet_name=(sheet_name or "").strip() or None,
            header_row=header_row,
        )
        return gpo_key.import_key(conn, supplier_id, path, file.filename or "key", columns)
    finally:
        os.unlink(path)


# --- Column maps -------------------------------------------------------------

@router.get("/{supplier_id}/column-mappings", responses=problems(404), response_model=list[ColumnMapping], tags=["column maps"])
def list_column_mappings(conn: Conn, supplier_id: int) -> list[ColumnMapping]:
    return column_maps.list_mappings(conn, supplier_id)


@router.post("/{supplier_id}/column-mappings", responses=problems(404), response_model=ColumnMapping, tags=["column maps"],
             summary="Create or replace the mapping for a source column")
def save_column_mapping(conn: Conn, supplier_id: int, body: ColumnMappingInput) -> ColumnMapping:
    return column_maps.save_mapping(conn, supplier_id, body)


@router.post("/{supplier_id}/column-mappings/bulk", responses=problems(404), response_model=list[ColumnMapping], tags=["column maps"],
             summary="Save a reviewed set of inferred mappings")
def save_inferred_mappings(conn: Conn, supplier_id: int, body: list[InferredMappingInput]) -> list[ColumnMapping]:
    return column_maps.save_inferred_mappings(conn, supplier_id, body)


@router.post("/{supplier_id}/column-mappings/inference", responses=problems(404, 413), response_model=list[InferredMapping],
             tags=["column maps"], summary="Suggest mappings from a sample file's headers (nothing is saved)")
def infer_column_mappings(
    request: Request, conn: Conn, supplier_id: int, file: Annotated[UploadFile, File()],
) -> list[InferredMapping]:
    path = upload_to_temp_file(request, file)
    try:
        return column_maps.infer_from_sample(conn, supplier_id, path)
    finally:
        os.unlink(path)


# --- Tier C: extraction rules ------------------------------------------------

@router.get("/{supplier_id}/extraction-rules", responses=problems(404), response_model=list[ExtractionRule], tags=["tier c"])
def list_extraction_rules(conn: Conn, supplier_id: int) -> list[ExtractionRule]:
    return tier_c.list_extraction_rules(conn, supplier_id)


@router.post("/{supplier_id}/extraction-rules", responses=problems(404, 409), response_model=ExtractionRuleSaveResult,
             status_code=status.HTTP_201_CREATED, tags=["tier c"])
def create_extraction_rule(conn: Conn, supplier_id: int, body: ExtractionRuleInput) -> ExtractionRuleSaveResult:
    return tier_c.save_extraction_rule(conn, supplier_id, body)


@router.put("/{supplier_id}/extraction-rules/{rule_id}", responses=problems(404, 409), response_model=ExtractionRuleSaveResult, tags=["tier c"],
            summary="Replace an extraction rule; a rename also renames referencing allocation rules")
def update_extraction_rule(
    conn: Conn, supplier_id: int, rule_id: int, body: ExtractionRuleInput,
) -> ExtractionRuleSaveResult:
    return tier_c.save_extraction_rule(conn, supplier_id, body, rule_id=rule_id)


@router.delete("/{supplier_id}/extraction-rules/{rule_id}", responses=problems(404, 409), status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response, tags=["tier c"],
               summary="Delete an extraction rule (409 while allocation rules reference it)")
def delete_extraction_rule(conn: Conn, supplier_id: int, rule_id: int) -> Response:
    tier_c.delete_extraction_rule(conn, supplier_id, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Tier C: allocation rules ------------------------------------------------

@router.get("/{supplier_id}/allocation-rules", responses=problems(404), response_model=list[AllocationRule], tags=["tier c"])
def list_allocation_rules(conn: Conn, supplier_id: int) -> list[AllocationRule]:
    return tier_c.list_allocation_rules(conn, supplier_id)


@router.post("/{supplier_id}/allocation-rules", responses=problems(404, 409), response_model=AllocationRule,
             status_code=status.HTTP_201_CREATED, tags=["tier c"])
def create_allocation_rule(conn: Conn, supplier_id: int, body: AllocationRuleInput) -> AllocationRule:
    return tier_c.save_allocation_rule(conn, supplier_id, body)


@router.put("/{supplier_id}/allocation-rules/{rule_id}", responses=problems(404, 409), response_model=AllocationRule, tags=["tier c"])
def update_allocation_rule(conn: Conn, supplier_id: int, rule_id: int, body: AllocationRuleInput) -> AllocationRule:
    return tier_c.save_allocation_rule(conn, supplier_id, body, rule_id=rule_id)


@router.delete("/{supplier_id}/allocation-rules/{rule_id}", responses=problems(404), status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response, tags=["tier c"])
def delete_allocation_rule(conn: Conn, supplier_id: int, rule_id: int) -> Response:
    tier_c.delete_allocation_rule(conn, supplier_id, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{supplier_id}/allocation-rules/validation", responses=problems(404), response_model=list[Notice], tags=["tier c"],
             summary="Advisory warnings for an unsaved allocation rule (percent totals, duplicate remainder)")
def validate_allocation_rule(conn: Conn, supplier_id: int, body: AllocationRuleDraft) -> list[Notice]:
    return tier_c.validate_allocation_draft(conn, supplier_id, body)
