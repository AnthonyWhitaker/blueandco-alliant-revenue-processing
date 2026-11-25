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
# ZIP / city steps, when both the row and the member have a street:
FUZZY_STRONG_NAME_THRESHOLD = 95  # name score that auto-matches even though the streets differ
FUZZY_STREET_NAME_THRESHOLD = 60  # weaker name score (contained names count) accepted when street + ZIP are equal
FUZZY_STREET_CONFIDENCE     = 90  # confidence of a ZIP / city match confirmed by street + ZIP (at least)
AUTO_MATCH_MIN_CONFIDENCE   = 80  # default supplier_profile.auto_match_min_confidence: below it, review instead
REVIEW_SUGGESTION_MIN_SCORE = 50  # review candidates need at least this name score (or a JDE / GPO ID / address hit)
REVIEW_MAX_CANDIDATES       = 3

# ---------------------------------------------------------------------------
# Match methods (stored in revenue_row.match_method)
# ---------------------------------------------------------------------------
MATCH_JDE_HISTORY   = "jde_history"
MATCH_ALIAS         = "alias"
MATCH_ZIP_STATE     = "zip_state"
MATCH_CITY_STATE    = "city_state"
MATCH_STATE         = "state"
MATCH_NATIONWIDE    = "nationwide"
# ZIP / city step matches confirmed by an equal street + ZIP (a weaker name score is enough).
MATCH_ZIP_STATE_STREET  = "zip_state_street"
MATCH_CITY_STATE_STREET = "city_state_street"
# Unmatched, to review: fuzzy candidates that may not auto-match (statewide / nationwide results,
# or a similar name in the same ZIP / city at a different street).
MATCH_FUZZY_SUGGESTION  = "fuzzy_suggestion"
# Unmatched, to review: a match whose confidence (match_score) is below the supplier's
# auto_match_min_confidence. The member it would have been is the review item's first candidate.
MATCH_LOW_CONFIDENCE    = "low_confidence"
# A reviewer said this account is not a member (non_member table): no review item, kept out of the output.
MATCH_NOT_A_MEMBER      = "not_a_member"
# gpo_key_then_member: the row failed the GPO key; member matching's result is a review candidate only.
MATCH_KEY_FALLBACK      = "gpo_key_fallback"
MATCH_UNMATCHED     = "unmatched"
MATCH_ENTITY_CODE   = "entity_code_exact"
# An entity code shared by several members (ship-to sites): which rule picked the site.
MATCH_ENTITY_CODE_SITE_ADDRESS = "entity_code_site_address"  # row street + ZIP equal one site's
MATCH_ENTITY_CODE_PRIMARY      = "entity_code_primary_site"  # else the site flagged primary
MATCH_ENTITY_CODE_NO_PRIMARY   = "entity_code_no_primary"    # else unmatched, to review (not a match)
# Until the Account List is reloaded with its primary-site column, no member has a primary flag at all.
# Meanwhile rule 3 keeps the previous behaviour (the last site loaded, highest member id) instead of review.
MATCH_ENTITY_CODE_UNFLAGGED    = "entity_code_unflagged_site"
MATCH_ACCOUNT_ID    = "account_id_exact"
# Unmatched, to review: the GPO ID (entity code hint) didn't fit the row and no later step matched it,
# or the JDE number and the GPO ID point to different members.
MATCH_GPO_ID_CONFLICT     = "gpo_id_conflict"
MATCH_JDE_GPO_CONFLICT    = "jde_gpo_conflict"
# Default for supplier_profile.gpo_id_min_name_score: a GPO ID match is used only when the file's name
# scores at least this against the member it points to, or the street + ZIP match.
GPO_ID_MIN_NAME_SCORE     = 50
MATCH_PIVOT_ALLOCATION = "pivot_allocation"
MATCH_GPO_KEY       = "gpo_key"

# ---------------------------------------------------------------------------
# Supplier job types (supplier_profile.job_type) — selects which pipeline
# process_job() runs. Declarative dispatch only; never branch on supplier name.
# ---------------------------------------------------------------------------
JOB_TYPE_ROW_LEVEL       = "row_level"
JOB_TYPE_PIVOT_ALLOCATION = "pivot_allocation"

# ---------------------------------------------------------------------------
# Identity modes (supplier_profile.identity_mode) — how a row-level file's
# rows are tied to a facility.
#   member_match : MemberMatcher (exact hints, JDE history, aliases, fuzzy);
#                  unmatched customers go to the Review queue
#   gpo_key      : the GPO ID is looked up in the supplier's key file
#                  (gpo_key_entry) by account number + name + address; rows
#                  that don't verify go to the exceptions report, never guessed
#   gpo_key_then_member : gpo_key first; rows that fail because the account,
#                  name or address doesn't agree with the key (KEY_FALLBACK_REASONS)
#                  go through MemberMatcher and land in the Review queue, never
#                  auto-matched; other key failures go to the exceptions report
# ---------------------------------------------------------------------------
IDENTITY_MEMBER_MATCH = "member_match"
IDENTITY_GPO_KEY      = "gpo_key"
IDENTITY_GPO_KEY_THEN_MEMBER = "gpo_key_then_member"
# Modes that verify against a GPO key (need one loaded).
KEY_IDENTITY_MODES = (IDENTITY_GPO_KEY, IDENTITY_GPO_KEY_THEN_MEMBER)

# Volume modes (supplier_profile.volume_mode)
#   sum   : Volume = sum of sales_amount
#   blank : Volume left empty (files already rolled up that carry revenue only)
VOLUME_SUM   = "sum"
VOLUME_BLANK = "blank"

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
ROW_KEY_EXCEPTION   = "key_exception"   # gpo_key mode: not verified against the key
ROW_NOT_MEMBER      = "not_a_member"    # a reviewer said the account isn't a member: not in the output

# Reasons a row fails GPO key verification (revenue_row.exception_reason)
KEY_MISSING_ACCOUNT  = "missing_account_number"
KEY_ACCOUNT_NOT_FOUND = "account_not_in_key"
KEY_NAME_MISMATCH    = "name_mismatch"
KEY_ADDRESS_MISMATCH = "address_mismatch"
KEY_NAME_AND_ADDRESS_MISMATCH = "name_and_address_mismatch"
KEY_AMBIGUOUS        = "ambiguous_key_entries"
KEY_NO_GPO_ID        = "key_entry_has_no_gpo_id"
KEY_NOT_LOADED       = "key_not_loaded"
# gpo_key_then_member: key failures that go on to member matching (and review); the rest stay exceptions.
KEY_FALLBACK_REASONS = frozenset({
    KEY_MISSING_ACCOUNT, KEY_ACCOUNT_NOT_FOUND, KEY_NAME_MISMATCH, KEY_ADDRESS_MISMATCH,
    KEY_NAME_AND_ADDRESS_MISMATCH,
})

# ---------------------------------------------------------------------------
# Review item status values
# ---------------------------------------------------------------------------
REVIEW_PENDING      = "pending"
REVIEW_APPROVED     = "approved"
REVIEW_DISMISSED    = "dismissed"
REVIEW_SKIPPED      = "skipped"
REVIEW_NOT_MEMBER   = "not_member"

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
EXC_MANUAL_TOTAL          = "manual_total_entered"
EXC_KEY_VERIFICATION      = "gpo_key_verification"
EXC_GPO_ID_CORRECTED      = "gpo_id_corrected"
EXC_DUPLICATE_ROWS        = "duplicate_rows"
EXC_DUPLICATE_FILE        = "duplicate_file"
EXC_RECONCILIATION        = "reconciliation"
EXC_DROPPED_ROWS          = "dropped_rows"
EXC_TIMEFRAME_MISSING     = "timeframe_missing"

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
