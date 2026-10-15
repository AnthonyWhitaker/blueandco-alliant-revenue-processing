"""Dashboard, aliases, reference metadata, and background tasks."""

from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel, InputModel
from .enums import (
    AliasScope, AllocationType, CanonicalField, ExtractionMethod, JobType,
    LabelMatchMode, RangeAxis, SupplierPaymentIdSource, TaskKind, TaskStatus,
)
from .members import LedgerImportResult, MemberImportResult


class DashboardStats(ApiModel):
    active_members: int
    suppliers: int
    jobs: int
    pending_reviews: int


class Alias(ApiModel):
    id: int
    member_id: int
    member_name: str
    entity_code: Optional[str]
    raw_name: str
    jde_number: Optional[str]
    supplier_profile_id: Optional[int]
    supplier_name: Optional[str]
    approved_by: Optional[str]
    approved_at: Optional[datetime]


class AliasInput(InputModel):
    member_id: int
    raw_name: str = Field(min_length=1)
    jde_number: Optional[str] = None
    # None = global alias (all suppliers).
    supplier_profile_id: Optional[int] = None


class Meta(ApiModel):
    """Option lists for selects. Static for the life of a deployment."""

    canonical_fields: list[CanonicalField]
    revenue_types: list[str]
    keep_return_options: list[str]
    payment_id_sources: list[SupplierPaymentIdSource]
    job_types: list[JobType]
    extraction_methods: list[ExtractionMethod]
    label_match_modes: list[LabelMatchMode]
    range_axes: list[RangeAxis]
    allocation_types: list[AllocationType]
    alias_scopes: list[AliasScope]
    upload_extensions: list[str]


class Task(ApiModel):
    id: str
    kind: TaskKind
    status: TaskStatus
    created_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    member_import: Optional[MemberImportResult]
    ledger_import: Optional[LedgerImportResult]
