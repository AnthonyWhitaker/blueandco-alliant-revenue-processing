"""
Shared constants for the Alliant Revenue Processing Engine.
"""

# ---------------------------------------------------------------------------
# Canonical field names — these are what the engine works with internally.
# Supplier column maps translate raw column names into these.
# ---------------------------------------------------------------------------
CANONICAL_CUSTOMER_NAME     = "customer_name"
CANONICAL_JDE_NUMBER        = "jde_number"
CANONICAL_PROCESS_DATE      = "process_date"
CANONICAL_CITY              = "city"
CANONICAL_STATE             = "state"
CANONICAL_ZIP               = "zip"
CANONICAL_ADDRESS           = "address"
CANONICAL_SALES_AMOUNT      = "sales_amount"
CANONICAL_FEE_AMOUNT        = "fee_amount"
CANONICAL_PRODUCT_LINE      = "product_line"
CANONICAL_CONTRACT_NUMBER   = "contract_number"
# Supplier-provided identity hints — used for exact-match fast paths ahead of
# fuzzy name matching. Populated when the supplier file already carries a
# Premier/Alliant Entity Code or a Salesforce Account ID per row (e.g. Viscot,
# Zier, Bon Secours, Harbinger, Propio).
CANONICAL_ENTITY_CODE_HINT  = "entity_code_hint"
CANONICAL_ACCOUNT_ID_HINT   = "account_id_hint"

# fee_amount is always required from the file. process_date can be omitted
# from the file if the filename carries a date (e.g. Carolon) — the
# processor falls back to parse_date_from_filename(). Customer identity can
# be established via customer_name, jde_number, OR one of the exact-match
# hints above — at least one of REQUIRED_IDENTITY_FIELDS_ANY must be present.
REQUIRED_CANONICAL_FIELDS = [
    CANONICAL_FEE_AMOUNT,
]
REQUIRED_IDENTITY_FIELDS_ANY = [
    CANONICAL_CUSTOMER_NAME,
    CANONICAL_JDE_NUMBER,
    CANONICAL_ENTITY_CODE_HINT,
    CANONICAL_ACCOUNT_ID_HINT,
]

# ---------------------------------------------------------------------------
# Default fuzzy matching thresholds (can be overridden per supplier profile)
# ---------------------------------------------------------------------------
FUZZY_ZIP_STATE_THRESHOLD   = 75
FUZZY_CITY_STATE_THRESHOLD  = 75
FUZZY_STATE_THRESHOLD       = 80
FUZZY_NATIONWIDE_THRESHOLD  = 85
FUZZY_MIN_NAME_LENGTH       = 5   # names shorter than this skip nationwide match

# ---------------------------------------------------------------------------
# Match methods (stored in revenue_row.match_method)
# ---------------------------------------------------------------------------
MATCH_JDE_HISTORY   = "jde_history"
MATCH_ALIAS         = "alias"
MATCH_ZIP_STATE     = "zip_state"
MATCH_CITY_STATE    = "city_state"
MATCH_STATE         = "state"
MATCH_NATIONWIDE    = "nationwide"
MATCH_UNMATCHED     = "unmatched"
MATCH_ENTITY_CODE   = "entity_code_exact"
MATCH_ACCOUNT_ID    = "account_id_exact"
MATCH_PIVOT_ALLOCATION = "pivot_allocation"

# ---------------------------------------------------------------------------
# Supplier job types (supplier_profile.job_type) — selects which pipeline
# process_job() runs. Declarative dispatch only; never branch on supplier name.
# ---------------------------------------------------------------------------
JOB_TYPE_ROW_LEVEL       = "row_level"
JOB_TYPE_PIVOT_ALLOCATION = "pivot_allocation"

# ---------------------------------------------------------------------------
# Extraction methods (supplier_extraction_rule.extraction_method)
# ---------------------------------------------------------------------------
EXTRACT_NAMED_CELL      = "named_cell"
EXTRACT_LABEL_LOOKUP    = "label_lookup"
EXTRACT_LABEL_RANGE_SUM = "label_range_sum"
EXTRACT_COLUMN_SUM      = "column_sum"
EXTRACT_FORMULA         = "formula"
EXTRACT_MANUAL_ENTRY    = "manual_entry"

# ---------------------------------------------------------------------------
# Allocation types (supplier_allocation_rule.allocation_type)
# ---------------------------------------------------------------------------
ALLOC_PERCENT       = "percent"
ALLOC_FIXED_AMOUNT  = "fixed_amount"
ALLOC_REMAINDER     = "remainder"

# ---------------------------------------------------------------------------
# Job status values
# ---------------------------------------------------------------------------
STATUS_UPLOADED     = "uploaded"
STATUS_VALIDATING   = "validating"
STATUS_VALIDATED    = "validated"
STATUS_PROCESSING   = "processing"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_COMPLETED    = "completed"
STATUS_FAILED       = "failed"

# ---------------------------------------------------------------------------
# Match status values (per row)
# ---------------------------------------------------------------------------
ROW_MATCHED         = "matched"
ROW_UNMATCHED       = "unmatched"
ROW_NEEDS_REVIEW    = "needs_review"
ROW_ALIAS_MATCHED   = "alias_matched"

# ---------------------------------------------------------------------------
# Review item status values
# ---------------------------------------------------------------------------
REVIEW_PENDING      = "pending"
REVIEW_APPROVED     = "approved"
REVIEW_DISMISSED    = "dismissed"
REVIEW_SKIPPED      = "skipped"

# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------
EXC_MISSING_COLUMN      = "missing_required_column"
EXC_UNMATCHED_CUSTOMER  = "unmatched_customer"
EXC_AMBIGUOUS_MATCH     = "ambiguous_match"
EXC_PAYMENT_NOT_FOUND   = "payment_id_not_found"
EXC_REVENUE_MISMATCH    = "revenue_reconciliation_mismatch"
EXC_DATE_PARSE          = "date_parse_error"
EXC_PROCESSING_ERROR    = "processing_error"
EXC_EXTRACTION_NOT_FOUND  = "extraction_not_found"
EXC_EXTRACTION_AMBIGUOUS  = "extraction_ambiguous"
EXC_ALLOCATION_INCOMPLETE = "allocation_incomplete"
EXC_MISSING_ENTITY_CODE   = "missing_entity_code"

# ---------------------------------------------------------------------------
# Export artifact types
# ---------------------------------------------------------------------------
ARTIFACT_SF_UPLOAD      = "salesforce_upload"
ARTIFACT_VALIDATION     = "validation_report"
ARTIFACT_EXCEPTION      = "exception_report"
ARTIFACT_JDE_MAPPING    = "jde_mapping"

# ---------------------------------------------------------------------------
# Salesforce upload column order (matches Data Upload Template.xlsx)
# ---------------------------------------------------------------------------
SF_UPLOAD_COLUMNS = [
    "Pec",
    "Facility ID",
    "Contract ID",
    "Payment ID",
    "Volume",
    "Total Revenue",
    "Revenue Type",
    "Keep/Return?",
    "Earned Period",
    "Pmt For (Time Frame)",
    "Date Received",
    "Comments",
    "Date Refunded",
    "Rebate Retained",
    "Amount Retained",
    "Retained Reason",
]

# ---------------------------------------------------------------------------
# Legal entity suffixes to strip during name normalization
# ---------------------------------------------------------------------------
LEGAL_SUFFIXES = [
    r"\bINC\.?\b", r"\bLLC\.?\b", r"\bLP\.?\b", r"\bLTD\.?\b",
    r"\bCORP\.?\b", r"\bCO\.?\b", r"\bPLC\.?\b", r"\bPA\.?\b",
    r"\bPC\.?\b", r"\bLLP\.?\b", r"\bPLLC\.?\b", r"\bNPC\.?\b",
    r"\bDBA\.?\b", r"\bINC$", r"\bLLC$",
]

# ---------------------------------------------------------------------------
# US state abbreviation → full name mapping (used in geographic normalization)
# ---------------------------------------------------------------------------
STATE_ABBREV = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS",
    "CA": "CALIFORNIA", "CO": "COLORADO", "CT": "CONNECTICUT",
    "DE": "DELAWARE", "FL": "FLORIDA", "GA": "GEORGIA", "HI": "HAWAII",
    "ID": "IDAHO", "IL": "ILLINOIS", "IN": "INDIANA", "IA": "IOWA",
    "KS": "KANSAS", "KY": "KENTUCKY", "LA": "LOUISIANA", "ME": "MAINE",
    "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN",
    "MN": "MINNESOTA", "MS": "MISSISSIPPI", "MO": "MISSOURI",
    "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA",
    "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO",
    "NY": "NEW YORK", "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA",
    "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON", "PA": "PENNSYLVANIA",
    "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT",
    "VA": "VIRGINIA", "WA": "WASHINGTON", "WV": "WEST VIRGINIA",
    "WI": "WISCONSIN", "WY": "WYOMING", "DC": "DISTRICT OF COLUMBIA",
    "PR": "PUERTO RICO", "VI": "VIRGIN ISLANDS",
}
STATE_FULL_TO_ABBREV = {v: k for k, v in STATE_ABBREV.items()}
