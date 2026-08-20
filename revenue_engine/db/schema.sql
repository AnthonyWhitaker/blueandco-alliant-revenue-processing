-- =============================================================================
-- Alliant Revenue Processing Engine — Database Schema
-- Compatible with SQLite (prototype) and Azure SQL (production)
-- Based on alliant-purchasing-solutions schema patterns
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SUPPLIER PROFILES
-- One row per supplier (Guerbet, Medline, Conmed, Amerisource, etc.)
-- Defines how to read their file and what Salesforce fields to populate.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supplier_profile (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL UNIQUE,          -- "Guerbet"
    description             TEXT,
    contract_id             TEXT,                          -- Salesforce Contract ID
    revenue_type            TEXT NOT NULL DEFAULT 'Admin Fee',
    keep_return_default     TEXT NOT NULL DEFAULT 'Keep',
    -- Column name variations handled via supplier_column_map
    -- Fuzzy matching thresholds (override engine defaults)
    fuzzy_zip_state_threshold   INTEGER NOT NULL DEFAULT 75,
    fuzzy_city_state_threshold  INTEGER NOT NULL DEFAULT 75,
    fuzzy_state_threshold       INTEGER NOT NULL DEFAULT 80,
    fuzzy_nationwide_threshold  INTEGER NOT NULL DEFAULT 85,
    -- File reading hints
    header_row              INTEGER NOT NULL DEFAULT 0,    -- 0-based row index for header
    sheet_name              TEXT,                          -- NULL = first sheet
    skip_rows_where_col_null TEXT,                         -- strip summary rows (e.g., "JDE Customer Number")
    max_read_columns        INTEGER,                       -- limit columns read (e.g. 22 for Carolon's wide stray-fill files)
    -- Payment ID resolution
    payment_id_source       TEXT NOT NULL DEFAULT 'ledger', -- 'ledger' | 'manual' | 'column'
    payor_filter_name       TEXT,                          -- name to filter ledger on (e.g., "Guerbet")
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- SUPPLIER COLUMN MAPPINGS
-- Maps each supplier's raw column names to canonical field names.
-- Canonical fields the engine understands:
--   customer_name, jde_number, process_date, city, state, zip,
--   address, sales_amount, fee_amount, product_line, contract_number
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supplier_column_map (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_profile_id     INTEGER NOT NULL REFERENCES supplier_profile(id) ON DELETE CASCADE,
    source_column           TEXT NOT NULL,    -- exact column name in raw file
    canonical_field         TEXT NOT NULL,    -- engine canonical name
    is_required             INTEGER NOT NULL DEFAULT 0,  -- 1=required, 0=optional
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(supplier_profile_id, source_column)
);

-- ---------------------------------------------------------------------------
-- MEMBER (FACILITY) MASTER
-- Loaded from Account List.xlsx — the central matching target.
-- Equivalent to alliant-purchasing-solutions dbo.company.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS member (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id              TEXT UNIQUE,       -- Salesforce Account ID / Facility ID
    premier_entity_code     TEXT,              -- PEC (preferred)
    alliant_entity_code     TEXT,              -- Fallback if PEC is blank
    entity_code             TEXT,              -- Computed: PEC if set, else Alliant code
    name                    TEXT NOT NULL,     -- Display name from Account List
    canonical_key           TEXT NOT NULL,     -- Normalized/cleaned name for matching
    billing_city            TEXT,
    billing_state           TEXT,
    billing_zip             TEXT,
    -- Pre-normalized fields (stored for fast matching without re-normalizing)
    norm_city               TEXT,
    norm_state              TEXT,
    norm_zip                TEXT,
    is_active               INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_member_canonical_key ON member(canonical_key);
CREATE INDEX IF NOT EXISTS ix_member_entity_code   ON member(entity_code);
CREATE INDEX IF NOT EXISTS ix_member_zip            ON member(norm_zip);
CREATE INDEX IF NOT EXISTS ix_member_state          ON member(norm_state);

-- ---------------------------------------------------------------------------
-- MEMBER ALIASES
-- Known alternate names for a member — approved by a user during review.
-- Equivalent to alliant-purchasing-solutions dbo.company_alias.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS member_alias (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id               INTEGER NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    supplier_profile_id     INTEGER REFERENCES supplier_profile(id) ON DELETE SET NULL,
    -- NULL supplier_profile_id = global alias (applies to all suppliers)
    raw_name                TEXT NOT NULL,     -- The alternate name (e.g., "OH MUHLENBERG LLC")
    canonical_key           TEXT NOT NULL,     -- Normalized version
    jde_number              TEXT,              -- If alias is tied to a JDE Customer Number
    approved_by             TEXT,
    approved_at             TIMESTAMP,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(supplier_profile_id, canonical_key)
);

CREATE INDEX IF NOT EXISTS ix_member_alias_key ON member_alias(canonical_key);
CREATE INDEX IF NOT EXISTS ix_member_alias_jde ON member_alias(jde_number);

-- ---------------------------------------------------------------------------
-- PAYMENT LEDGER
-- Loaded from Ledger.xlsx — maps deposit amounts to Salesforce Payment IDs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_ledger (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    payor                   TEXT NOT NULL,       -- Supplier name (e.g., "Guerbet")
    payment_received_date   DATE,
    deposit_amount          REAL NOT NULL,
    sf_payment_id           TEXT NOT NULL,       -- Salesforce Payment ID
    contract_id             TEXT,
    notes                   TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ledger_payor        ON payment_ledger(payor COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_ledger_deposit      ON payment_ledger(deposit_amount);

-- ---------------------------------------------------------------------------
-- REVENUE IMPORT JOB
-- One row per file processed. Tracks lifecycle from upload to export.
-- Equivalent to alliant-purchasing-solutions dbo.import_job.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revenue_job (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_profile_id     INTEGER NOT NULL REFERENCES supplier_profile(id),
    original_file_name      TEXT NOT NULL,
    file_path               TEXT,              -- Local path or blob path
    status                  TEXT NOT NULL DEFAULT 'uploaded',
    -- Status values: uploaded → validating → validated → processing → needs_review
    --                → completed → failed
    payment_id              TEXT,              -- Resolved Salesforce Payment ID
    payment_id_source       TEXT,              -- 'ledger_auto' | 'manual' | 'column'
    deposit_amount          REAL,              -- Parsed from filename or column
    date_received           DATE,              -- Date file/deposit received
    earned_period_start     DATE,              -- Earliest earned period in file
    earned_period_end       DATE,              -- Latest earned period in file
    contract_id             TEXT,              -- Effective contract ID for this job
    row_count               INTEGER,
    matched_count           INTEGER,
    unmatched_count         INTEGER,
    total_volume            REAL,              -- Sum of sales_amount
    total_revenue           REAL,              -- Sum of fee_amount
    error_message           TEXT,
    column_mapping_json     TEXT,              -- Confirmed column→canonical mapping
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at            TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- REVENUE ROW
-- One row per line in the raw supplier file.
-- Stores raw values, parsed values, and match results.
-- Equivalent to alliant-purchasing-solutions dbo.import_row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revenue_row (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                  INTEGER NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
    row_number              INTEGER NOT NULL,
    raw_row_json            TEXT,              -- Full original row as JSON
    -- Parsed canonical fields
    jde_number              TEXT,
    customer_name           TEXT,
    process_date            DATE,
    city                    TEXT,
    state                   TEXT,
    zip                     TEXT,
    address                 TEXT,
    sales_amount            REAL,
    fee_amount              REAL,
    product_line            TEXT,
    contract_number         TEXT,
    -- Member matching results
    matched_member_id       INTEGER REFERENCES member(id),
    match_method            TEXT,   -- 'jde_history'|'zip_state'|'city_state'|'state'|'nationwide'|'alias'|'unmatched'
    match_score             INTEGER,
    match_geo_filter        TEXT,   -- Which geographic filter was active during match
    entity_code             TEXT,   -- PEC or Alliant code from matched member
    account_id              TEXT,   -- Salesforce Account ID from matched member
    account_name_db         TEXT,   -- Canonical name from Account List
    -- Processing status
    match_status            TEXT NOT NULL DEFAULT 'pending',
    -- Values: pending | matched | unmatched | needs_review | alias_matched
    validation_status       TEXT NOT NULL DEFAULT 'pending',
    -- Values: pending | valid | invalid
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_rev_row_job        ON revenue_row(job_id);
CREATE INDEX IF NOT EXISTS ix_rev_row_jde        ON revenue_row(jde_number);
CREATE INDEX IF NOT EXISTS ix_rev_row_status     ON revenue_row(match_status);
CREATE INDEX IF NOT EXISTS ix_rev_row_member     ON revenue_row(matched_member_id);

-- ---------------------------------------------------------------------------
-- MEMBER REVIEW ITEM
-- Rows that couldn't be auto-matched — presented to user for decision.
-- Equivalent to alliant-purchasing-solutions dbo.review_item.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS member_review_item (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                      INTEGER NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
    -- The unmatched customer info (grouped by JDE number or name if no JDE)
    jde_number                  TEXT,
    customer_name_sample        TEXT,   -- Representative raw name
    canonical_key               TEXT,   -- Normalized version
    city                        TEXT,
    state                       TEXT,
    zip                         TEXT,
    -- Best suggestion from fuzzy engine
    suggested_member_id         INTEGER REFERENCES member(id),
    suggested_match_score       INTEGER,
    -- Impact metrics
    total_fee                   REAL,
    total_sales                 REAL,
    affected_row_count          INTEGER,
    -- Review outcome
    status                      TEXT NOT NULL DEFAULT 'pending',
    -- Values: pending | approved | dismissed | skipped
    resolved_member_id          INTEGER REFERENCES member(id),
    resolution_scope            TEXT,   -- 'job_only' | 'supplier' | 'global'
    resolved_by                 TEXT,
    resolved_at                 TIMESTAMP,
    notes                       TEXT,
    created_at                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_review_job    ON member_review_item(job_id);
CREATE INDEX IF NOT EXISTS ix_review_status ON member_review_item(status);

-- ---------------------------------------------------------------------------
-- REVENUE OUTPUT ROW
-- One row per (PEC × earned_period) aggregated result — the upload tab.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revenue_output_row (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                  INTEGER NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
    pec                     TEXT NOT NULL,       -- Entity Code
    facility_id             TEXT,                -- Salesforce Account ID
    contract_id             TEXT,
    payment_id              TEXT,
    volume                  REAL,                -- Sum of sales_amount
    total_revenue           REAL,                -- Sum of fee_amount
    revenue_type            TEXT NOT NULL DEFAULT 'Admin Fee',
    keep_return             TEXT NOT NULL DEFAULT 'Keep',
    earned_period           DATE,                -- First of month
    quarter_label           TEXT,                -- e.g., "3Q2025"
    date_received           DATE,
    comments                TEXT,
    date_refunded           DATE,
    rebate_retained         TEXT,
    amount_retained         REAL,
    retained_reason         TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_output_job ON revenue_output_row(job_id);
CREATE INDEX IF NOT EXISTS ix_output_pec ON revenue_output_row(pec);

-- ---------------------------------------------------------------------------
-- EXCEPTION
-- Processing errors and warnings, tied to job and optionally to a row.
-- Equivalent to alliant-purchasing-solutions dbo.exception.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exception (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                  INTEGER NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
    row_id                  INTEGER REFERENCES revenue_row(id) ON DELETE SET NULL,
    exception_type          TEXT NOT NULL,
    -- Types: missing_required_column | unmatched_customer | ambiguous_match
    --        | payment_id_not_found | revenue_reconciliation_mismatch
    --        | date_parse_error | processing_error
    message                 TEXT NOT NULL,
    severity                TEXT NOT NULL DEFAULT 'warning',
    -- Values: info | warning | error
    status                  TEXT NOT NULL DEFAULT 'open',
    -- Values: open | resolved | acknowledged
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_exc_job      ON exception(job_id);
CREATE INDEX IF NOT EXISTS ix_exc_type     ON exception(exception_type);
CREATE INDEX IF NOT EXISTS ix_exc_severity ON exception(severity);

-- ---------------------------------------------------------------------------
-- EXPORT FILE
-- References to generated output files (Upload XLSX, Exception reports, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS export_file (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                  INTEGER NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
    artifact_type           TEXT NOT NULL,
    -- Types: salesforce_upload | validation_report | exception_report | jde_mapping
    file_name               TEXT NOT NULL,
    file_path               TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- JDE MAPPING (cross-job history for faster re-matching)
-- Tracks JDE Customer Number → member_id mappings learned across jobs.
-- Equivalent to the "previous mapping" pattern in guerbet_processor.py
-- but stored persistently in the DB instead of re-read from processed files.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jde_member_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_profile_id     INTEGER NOT NULL REFERENCES supplier_profile(id) ON DELETE CASCADE,
    jde_number              TEXT NOT NULL,
    member_id               INTEGER NOT NULL REFERENCES member(id),
    first_seen_job_id       INTEGER REFERENCES revenue_job(id),
    last_seen_job_id        INTEGER REFERENCES revenue_job(id),
    occurrence_count        INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(supplier_profile_id, jde_number)
);

CREATE INDEX IF NOT EXISTS ix_jde_history_supplier ON jde_member_history(supplier_profile_id);
CREATE INDEX IF NOT EXISTS ix_jde_history_jde      ON jde_member_history(jde_number);

-- ---------------------------------------------------------------------------
-- SCHEMA METADATA
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

INSERT OR IGNORE INTO schema_version(version, description)
VALUES (1, 'Initial revenue processing engine schema');
