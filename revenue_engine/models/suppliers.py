from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel, InputModel
from .enums import IdentityMode, JobType, SupplierPaymentIdSource, VolumeMode


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
    identity_mode: IdentityMode
    volume_mode: VolumeMode
    fuzzy_zip_state_threshold: int
    fuzzy_city_state_threshold: int
    fuzzy_state_threshold: int
    fuzzy_nationwide_threshold: int
    fuzzy_strong_name_threshold: int
    fuzzy_street_name_threshold: int
    auto_match_min_confidence: int
    gpo_id_min_name_score: int
    created_at: datetime
    updated_at: datetime


class SupplierInput(InputModel):
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
    identity_mode: IdentityMode = Field(
        default="member_match",
        description="Row-level only. 'member_match': fuzzy/exact member matching with a Review queue. "
                    "'gpo_key': GPO ID verified against the supplier's key file; unverified rows go to the "
                    "exceptions report. 'gpo_key_then_member': gpo_key first; rows whose account, name or address "
                    "doesn't agree with the key go through member matching to the Review queue (never auto-matched).",
    )
    volume_mode: VolumeMode = Field(
        default="sum", description="'sum': Volume = sum of sales amounts. 'blank': Volume is left empty.",
    )
    fee_rate: Optional[float] = Field(default=None, ge=0)
    fuzzy_zip_state_threshold: int = Field(default=75, ge=0, le=100)
    fuzzy_city_state_threshold: int = Field(default=75, ge=0, le=100)
    fuzzy_state_threshold: int = Field(default=80, ge=0, le=100)
    fuzzy_nationwide_threshold: int = Field(default=85, ge=0, le=100)
    fuzzy_strong_name_threshold: int = Field(
        default=95, ge=0, le=100,
        description="ZIP/city steps: a name score that auto-matches even though the row's street differs from the member's.",
    )
    fuzzy_street_name_threshold: int = Field(
        default=60, ge=0, le=100,
        description="ZIP/city steps: the name score that is enough when street + ZIP are equal (names contained in a longer one count).",
    )
    auto_match_min_confidence: int = Field(
        default=80, ge=0, le=100,
        description="member_match: a match whose confidence (match score) is below this goes to review instead.",
    )
    gpo_id_min_name_score: int = Field(
        default=50, ge=0, le=100,
        description="member_match: a GPO ID match is used only if the file's name scores at least this against "
                    "the member it points to, or the street + ZIP match.",
    )


class GpoKeySummary(ApiModel):
    """The GPO key currently loaded for a supplier (identity_mode = 'gpo_key')."""

    entry_count: int
    account_count: int
    source_file_name: Optional[str]
    loaded_at: Optional[datetime]


class GpoKeyImportResult(ApiModel):
    loaded: int
    skipped_blank_account: int = Field(description="Key rows without an account number (not loaded).")
    accounts_with_several_entries: int
    key: GpoKeySummary
