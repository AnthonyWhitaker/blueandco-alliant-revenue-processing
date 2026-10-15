"""
Tier C (pivot_allocation) rule schemas.

Required-field rules per extraction_method / allocation_type are enforced
here as field validators so a 422 points at the offending field.
"""

from typing import Optional

from pydantic import Field, ValidationInfo, field_validator

from .common import ApiModel, InputModel, Notice
from .enums import AllocationType, ExtractionMethod, LabelMatchMode, RangeAxis


class ExtractionRule(ApiModel):
    id: int
    supplier_profile_id: int
    rule_name: str
    sheet_name: Optional[str]
    extraction_method: ExtractionMethod
    cell_address: Optional[str]
    label_text: Optional[str]
    label_match_mode: Optional[LabelMatchMode]
    label_match_threshold: Optional[int]
    value_row_offset: Optional[int]
    value_col_offset: Optional[int]
    range_axis: Optional[RangeAxis]
    column_index: Optional[int]
    column_letter: Optional[str]
    header_row: Optional[int]
    stop_label_text: Optional[str]
    formula_expression: Optional[str]
    is_sales_amount: bool
    sort_order: int


def _method(info: ValidationInfo) -> Optional[str]:
    return info.data.get("extraction_method")


class ExtractionRuleInput(InputModel):
    # extraction_method comes first so field validators below can read it.
    extraction_method: ExtractionMethod
    rule_name: str = Field(min_length=1)
    sheet_name: Optional[str] = None
    cell_address: Optional[str] = Field(default=None, validate_default=True)
    label_text: Optional[str] = Field(default=None, validate_default=True)
    label_match_mode: LabelMatchMode = "exact"
    label_match_threshold: int = Field(default=90, ge=0, le=100)
    value_row_offset: int = 1
    value_col_offset: int = 0
    range_axis: RangeAxis = "column"
    column_letter: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{1,3}$")
    column_index: Optional[int] = Field(default=None, ge=0, validate_default=True)
    header_row: int = Field(default=0, ge=0)
    stop_label_text: Optional[str] = None
    formula_expression: Optional[str] = Field(default=None, validate_default=True)
    is_sales_amount: bool = False
    sort_order: int = 0

    @field_validator("cell_address")
    @classmethod
    def _cell_address_required(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        if _method(info) == "named_cell" and not v:
            raise ValueError("Cell Address is required for named_cell.")
        return v

    @field_validator("label_text")
    @classmethod
    def _label_required(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        if _method(info) in ("label_lookup", "label_range_sum") and not v:
            raise ValueError("Label Text is required for this method.")
        return v

    @field_validator("column_index")
    @classmethod
    def _column_required(cls, v: Optional[int], info: ValidationInfo) -> Optional[int]:
        if _method(info) == "column_sum" and v is None and not info.data.get("column_letter"):
            raise ValueError("Column Index or Column Letter is required for column_sum.")
        return v

    @field_validator("formula_expression")
    @classmethod
    def _formula_required(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        if _method(info) == "formula" and not v:
            raise ValueError("Formula Expression is required for formula.")
        return v


class ExtractionRuleSaveResult(ApiModel):
    rule: ExtractionRule
    renamed_allocation_refs: int
    notices: list[Notice]


class AllocationRule(ApiModel):
    id: int
    supplier_profile_id: int
    extraction_rule_name: str
    member_id: int
    member_name: str
    entity_code: Optional[str]
    allocation_type: AllocationType
    allocation_value: Optional[float]
    contract_number: Optional[str]
    product_line: Optional[str]
    is_active: bool


class AllocationRuleInput(InputModel):
    allocation_type: AllocationType
    extraction_rule_name: str = Field(min_length=1)
    member_id: int
    allocation_value: Optional[float] = Field(default=None, validate_default=True)
    contract_number: Optional[str] = None
    product_line: Optional[str] = None
    is_active: bool = True

    @field_validator("allocation_value")
    @classmethod
    def _value_required(cls, v: Optional[float], info: ValidationInfo) -> Optional[float]:
        if info.data.get("allocation_type") == "remainder":
            return None
        if not v:
            raise ValueError("Allocation Value is required for percent/fixed_amount.")
        return v


class AllocationRuleDraft(InputModel):
    """An unsaved allocation rule, checked against the saved ones for warnings."""

    allocation_type: AllocationType
    extraction_rule_name: str
    allocation_value: Optional[float] = None
    rule_id: Optional[int] = None
