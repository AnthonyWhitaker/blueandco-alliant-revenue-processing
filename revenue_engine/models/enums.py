"""
Closed value sets shared by API request/response models.

Values mirror engine/constants.py; tests/test_models.py asserts they stay in
sync. Free-text DB columns that the UI merely offers choices for
(revenue_type, keep_return_default) are deliberately not listed here.
"""

from typing import Literal

CanonicalField = Literal[
    "customer_name", "jde_number", "process_date", "city", "state", "zip",
    "address", "sales_amount", "fee_amount", "product_line", "contract_number",
    "entity_code_hint", "account_id_hint",
]
JobStatus = Literal[
    "uploaded", "validating", "validated", "processing", "needs_review", "completed", "failed",
]
JobType = Literal["row_level", "pivot_allocation"]
SupplierPaymentIdSource = Literal["ledger", "manual", "column"]
JobPaymentIdSource = Literal["ledger_auto", "manual", "column", "not_found"]
ExtractionMethod = Literal[
    "named_cell", "label_lookup", "label_range_sum", "column_sum", "formula", "manual_entry",
]
LabelMatchMode = Literal["exact", "contains", "startswith", "fuzzy"]
RangeAxis = Literal["column", "row"]
AllocationType = Literal["percent", "fixed_amount", "remainder"]
AliasScope = Literal["supplier", "global", "job_only"]
ReviewStatus = Literal["pending", "approved", "dismissed", "skipped"]
Severity = Literal["info", "warning", "error"]
NoticeLevel = Literal["info", "warning"]
TaskKind = Literal["member_import", "ledger_import"]
TaskStatus = Literal["queued", "running", "succeeded", "failed"]
