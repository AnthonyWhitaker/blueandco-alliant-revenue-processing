from datetime import date, datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel, InputModel
from .enums import JobPaymentIdSource, JobStatus, JobType, Severity


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
    pending_review_count: int
    created_at: datetime
    completed_at: Optional[datetime]


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
