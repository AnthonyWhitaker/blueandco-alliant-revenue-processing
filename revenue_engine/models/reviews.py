from typing import Literal, Optional

from pydantic import Field

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


MatchedField = Literal["name", "street", "zip", "jde", "gpo_id"]


class ReviewCandidate(ApiModel):
    """A member the reviewer can pick, with the evidence for it."""

    member_id: int
    name: str
    entity_code: Optional[str]
    street: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    score: int = Field(description="Name score (0-100) against the file's name.")
    matched: list[MatchedField] = Field(description="Fields that agree with the file's row.")
    via: str = Field(description="How the candidate was found: jde, gpo_id, name, or the match step.")


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
    review_reason: Optional[str] = Field(
        default=None,
        description="Why the row needs review beyond 'no match': gpo_id_conflict, jde_gpo_conflict, "
                    "entity_code_no_primary, fuzzy_suggestion, low_confidence or gpo_key_fallback.",
    )
    key_reason: Optional[str] = Field(
        default=None,
        description="gpo_key_then_member: why the row failed the GPO key (account_not_in_key, name_mismatch, ...).",
    )
    candidates: list[ReviewCandidate] = Field(default_factory=list, description="Up to 3, best first.")


class ReviewApproval(InputModel):
    member_id: int
    create_alias: bool = True
    alias_scope: AliasScope = "supplier"
    notes: str = ""


class ReviewNotMember(InputModel):
    notes: str = ""


class ReviewDecisionResult(ApiModel):
    updated_rows: int
    job: Job
