from typing import Optional

from .common import ApiModel, InputModel
from .enums import AliasScope, ReviewStatus
from .jobs import Job


class ReviewSuggestion(ApiModel):
    member_id: int
    name: str
    entity_code: Optional[str]
    city: Optional[str]
    state: Optional[str]
    score: int


class ReviewItem(ApiModel):
    id: int
    job_id: int
    jde_number: Optional[str]
    customer_name_sample: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    total_fee: Optional[float]
    total_sales: Optional[float]
    affected_row_count: Optional[int]
    status: ReviewStatus
    suggestion: Optional[ReviewSuggestion]


class ReviewApproval(InputModel):
    member_id: int
    create_alias: bool = True
    alias_scope: AliasScope = "supplier"
    notes: str = ""


class ReviewDecisionResult(ApiModel):
    updated_rows: int
    job: Job
