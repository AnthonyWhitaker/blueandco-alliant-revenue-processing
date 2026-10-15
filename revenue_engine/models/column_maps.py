from pydantic import Field

from .common import ApiModel, InputModel
from .enums import CanonicalField


class ColumnMapping(ApiModel):
    id: int
    supplier_profile_id: int
    source_column: str
    canonical_field: CanonicalField
    is_required: bool


class ColumnMappingInput(InputModel):
    # Exact header text, or "contains:<substring>" for a pattern rule.
    source_column: str = Field(min_length=1)
    canonical_field: CanonicalField
    is_required: bool = False


class InferredMapping(ApiModel):
    source_column: str
    canonical_field: CanonicalField


class InferredMappingInput(InputModel):
    source_column: str = Field(min_length=1)
    canonical_field: CanonicalField
