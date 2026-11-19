import json
from datetime import date, datetime
from typing import Optional

from pydantic import Field, field_validator

from .common import ApiModel, InputModel
from .enums import ArtifactType, JobPaymentIdSource, JobStatus, JobType, Severity


class ReconciliationInput(ApiModel):
    rows: int
    volume: float
    revenue: float
    unparseable_volume: int = 0
    unparseable_revenue: int = 0
    dropped_rows: int = Field(0, description="Summary/total rows stripped before processing (not part of the input).")
    dropped_volume: float = 0.0
    dropped_revenue: float = 0.0
    rows_per_file: dict[str, int] = Field(default_factory=dict)


class Reconciliation(ApiModel):
    """Input totals = Upload tab output + exceptions report, checked on every run."""

    input: ReconciliationInput
    output_rows: int
    output_volume: Optional[float]
    output_revenue: float
    exception_rows: int
    exceptions_volume: float
    exceptions_revenue: float
    volume_checked: bool = Field(description="False when the supplier's Volume is left blank.")
    volume_difference: Optional[float]
    revenue_difference: float
    balanced: bool


class Job(ApiModel):
    id: int
    supplier_profile_id: int
    supplier_name: str
    job_type: JobType
    original_file_name: str
    status: JobStatus
    payment_id: Optional[str]
    payment_id_source: Optional[JobPaymentIdSource]
    deposit_amount: Optional[float]
    date_received: Optional[date]
    earned_period_start: Optional[date]
    earned_period_end: Optional[date]
    contract_id: Optional[str]
    row_count: Optional[int]
    matched_count: Optional[int]
    unmatched_count: Optional[int]
    total_volume: Optional[float]
    total_revenue: Optional[float]
    error_message: Optional[str]
    error_detail: Optional[str] = Field(
        default=None, description="Technical details of a processing failure (the traceback), for support.",
    )
    pending_review_count: int
    manual_totals: dict[str, float] = Field(
        default_factory=dict, validation_alias="manual_totals_json",
        description="Tier C: the totals entered at upload for manual_entry extraction rules, by rule name.",
    )
    timeframe: Optional[str] = Field(description='"Pmt For (Time Frame)" for every Upload row, entered at upload.')
    file_count: int = Field(description="Number of source files combined in this job.")
    reconciliation: Optional[Reconciliation] = Field(
        default=None, validation_alias="reconciliation_json",
        description="Totals check from the last processing run or review decision. Null before processing.",
    )
    created_at: datetime
    completed_at: Optional[datetime]
    archived_at: Optional[datetime] = Field(
        default=None, description="When the job was deleted. Deleted jobs are hidden from lists and can be restored.",
    )

    @field_validator("manual_totals", mode="before")
    @classmethod
    def _parse_manual_totals(cls, v):
        return json.loads(v) if isinstance(v, str) else (v or {})

    @field_validator("reconciliation", mode="before")
    @classmethod
    def _parse_reconciliation(cls, v):
        return json.loads(v) if isinstance(v, str) else v


class JobFile(ApiModel):
    """One source file of a job."""

    id: int
    original_file_name: str
    sha256: str
    row_count: Optional[int] = Field(description="Data rows read from this file (set when processing finishes).")
    created_at: datetime


class JobOutput(ApiModel):
    """A stored output file. Each export writes a new version; none is ever overwritten."""

    id: int
    artifact_type: ArtifactType
    file_name: str
    created_at: datetime
    available: bool = Field(description="False when the file is no longer on disk (e.g. lost in a server restart).")


class JobPatch(InputModel):
    payment_id: str = Field(min_length=1)


class JobException(ApiModel):
    id: int
    exception_type: str
    message: str
    severity: Severity
    status: str
    created_at: datetime


class UploadSheetRow(ApiModel):
    """One row of the Salesforce Facility Revenue upload (exporter 'Upload' tab)."""

    pec: str
    facility_id: Optional[str]
    contract_id: Optional[str]
    payment_id: Optional[str]
    volume: Optional[float]
    total_revenue: Optional[float]
    revenue_type: Optional[str]
    keep_return: Optional[str]
    earned_period: Optional[date]
    pmt_for_time_frame: Optional[str]
    date_received: Optional[date]
    comments: Optional[str]
    date_refunded: Optional[date]
    rebate_retained: Optional[str]
    amount_retained: Optional[float]
    retained_reason: Optional[str]


class ValidationSheetRow(ApiModel):
    jde_number: Optional[str]
    supplier_customer_name: Optional[str]
    entity_code: Optional[str]
    salesforce_account_id: Optional[str]
    account_list_name: Optional[str]
    city: Optional[str]
    state: Optional[str]
    match_method: Optional[str]
    score: Optional[int]


class UnmatchedSheetRow(ApiModel):
    row_number: int
    jde_number: Optional[str]
    customer_name: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    process_date: Optional[date]
    sales_amount: Optional[float]
    fee_amount: Optional[float]
    product_line: Optional[str]


class JdeMappingSheetRow(ApiModel):
    jde_number: Optional[str]
    supplier_name: Optional[str]
    entity_code: Optional[str]
    account_list_name: Optional[str]


class JobPreview(ApiModel):
    """The four tabs of the exported workbook, as rows."""

    upload: list[UploadSheetRow]
    validation: list[ValidationSheetRow]
    unmatched: list[UnmatchedSheetRow]
    jde_mapping: list[JdeMappingSheetRow]


class FileNameInference(ApiModel):
    file_name: str
    supplier_profile_id: Optional[int]
    supplier_name: Optional[str]
    date_received: Optional[date]
    deposit_amount: Optional[float]
