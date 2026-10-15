from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel, InputModel
from .enums import JobType, SupplierPaymentIdSource


class Supplier(ApiModel):
    id: int
    name: str
    description: Optional[str]
    contract_id: Optional[str]
    revenue_type: str
    keep_return_default: str
    payor_filter_name: Optional[str]
    payment_id_source: SupplierPaymentIdSource
    skip_rows_where_col_null: Optional[str]
    sheet_name: Optional[str]
    header_row: int
    max_read_columns: Optional[int]
    fee_rate: Optional[float]
    job_type: JobType
    requires_manual_confirmation: bool
    fuzzy_zip_state_threshold: int
    fuzzy_city_state_threshold: int
    fuzzy_state_threshold: int
    fuzzy_nationwide_threshold: int
    created_at: datetime
    updated_at: datetime


class SupplierFormInput(InputModel):
    """The fields the legacy Admin › Suppliers form edits. Upserting through
    this model leaves fee_rate and the fuzzy thresholds untouched."""

    name: str = Field(min_length=1)
    description: Optional[str] = None
    contract_id: Optional[str] = None
    revenue_type: str = "Admin Fee"
    keep_return_default: str = "Keep"
    payor_filter_name: Optional[str] = None
    payment_id_source: SupplierPaymentIdSource = "ledger"
    skip_rows_where_col_null: Optional[str] = None
    sheet_name: Optional[str] = None
    header_row: int = Field(default=0, ge=0)
    max_read_columns: Optional[int] = Field(default=None, ge=1)
    job_type: JobType = "row_level"
    requires_manual_confirmation: bool = False


class SupplierInput(SupplierFormInput):
    fee_rate: Optional[float] = Field(default=None, ge=0)
    fuzzy_zip_state_threshold: int = Field(default=75, ge=0, le=100)
    fuzzy_city_state_threshold: int = Field(default=75, ge=0, le=100)
    fuzzy_state_threshold: int = Field(default=80, ge=0, le=100)
    fuzzy_nationwide_threshold: int = Field(default=85, ge=0, le=100)
