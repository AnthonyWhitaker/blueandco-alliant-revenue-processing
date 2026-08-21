-- =============================================================================
-- Alliant Revenue Processing Engine — Database Schema (Azure SQL / T-SQL)
-- Converted from SQLite schema.sql
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SUPPLIER PROFILES
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'supplier_profile')
BEGIN
    CREATE TABLE supplier_profile (
        id                          INT IDENTITY(1,1) PRIMARY KEY,
        name                        NVARCHAR(500) NOT NULL UNIQUE,
        description                 NVARCHAR(MAX),
        contract_id                 NVARCHAR(500),
        revenue_type                NVARCHAR(500) NOT NULL DEFAULT 'Admin Fee',
        keep_return_default         NVARCHAR(500) NOT NULL DEFAULT 'Keep',
        fuzzy_zip_state_threshold   INT NOT NULL DEFAULT 75,
        fuzzy_city_state_threshold  INT NOT NULL DEFAULT 75,
        fuzzy_state_threshold       INT NOT NULL DEFAULT 80,
        fuzzy_nationwide_threshold  INT NOT NULL DEFAULT 85,
        header_row                  INT NOT NULL DEFAULT 0,
        sheet_name                  NVARCHAR(500),
        skip_rows_where_col_null    NVARCHAR(500),
        max_read_columns            INT,
        payment_id_source           NVARCHAR(500) NOT NULL DEFAULT 'ledger',
        payor_filter_name           NVARCHAR(500),
        -- Job type: which processing pipeline this supplier's files go through.
        --   'row_level'        : one row per facility per period (MemberMatcher) — the default
        --   'pivot_allocation' : aggregate/pivot workpapers with no facility-level detail —
        --                        extract totals (supplier_extraction_rule) and allocate to
        --                        pre-known facilities (supplier_allocation_rule) instead of matching
        job_type                    NVARCHAR(50) NOT NULL DEFAULT 'row_level',
        requires_manual_confirmation INT NOT NULL DEFAULT 0,
        created_at                  DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        updated_at                  DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

-- ---------------------------------------------------------------------------
-- SUPPLIER COLUMN MAPPINGS
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'supplier_column_map')
BEGIN
    CREATE TABLE supplier_column_map (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        supplier_profile_id     INT NOT NULL REFERENCES supplier_profile(id) ON DELETE CASCADE,
        source_column           NVARCHAR(500) NOT NULL,
        canonical_field         NVARCHAR(500) NOT NULL,
        is_required             INT NOT NULL DEFAULT 0,
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        UNIQUE(supplier_profile_id, source_column)
    );
END
GO

-- ---------------------------------------------------------------------------
-- MEMBER (FACILITY) MASTER
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'member')
BEGIN
    CREATE TABLE member (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        account_id              NVARCHAR(500) UNIQUE,
        premier_entity_code     NVARCHAR(500),
        alliant_entity_code     NVARCHAR(500),
        entity_code             NVARCHAR(500),
        name                    NVARCHAR(MAX) NOT NULL,
        canonical_key           NVARCHAR(500) NOT NULL,
        billing_city            NVARCHAR(500),
        billing_state           NVARCHAR(500),
        billing_zip             NVARCHAR(500),
        norm_city               NVARCHAR(500),
        norm_state              NVARCHAR(500),
        norm_zip                NVARCHAR(500),
        is_active               INT NOT NULL DEFAULT 1,
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        updated_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_member_canonical_key')
    CREATE INDEX ix_member_canonical_key ON member(canonical_key);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_member_entity_code')
    CREATE INDEX ix_member_entity_code ON member(entity_code);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_member_zip')
    CREATE INDEX ix_member_zip ON member(norm_zip);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_member_state')
    CREATE INDEX ix_member_state ON member(norm_state);
GO

-- ---------------------------------------------------------------------------
-- SUPPLIER EXTRACTION RULES  (Tier C — pivot_allocation job_type)
-- Declares WHERE to find each dollar total in an arbitrarily-shaped pivot /
-- summary workpaper. Generalization of supplier_column_map for suppliers
-- with no facility-level rows to map columns against.
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'supplier_extraction_rule')
BEGIN
    CREATE TABLE supplier_extraction_rule (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        supplier_profile_id     INT NOT NULL REFERENCES supplier_profile(id) ON DELETE CASCADE,
        rule_name               NVARCHAR(500) NOT NULL,
        sheet_name              NVARCHAR(500),
        extraction_method       NVARCHAR(50) NOT NULL,
        -- 'named_cell' | 'label_lookup' | 'label_range_sum' | 'column_sum' |
        -- 'formula' | 'manual_entry' — see schema.sql for full method docs
        cell_address            NVARCHAR(50),
        label_text              NVARCHAR(500),
        label_match_mode        NVARCHAR(50) DEFAULT 'exact',
        label_match_threshold   INT DEFAULT 90,
        value_row_offset        INT DEFAULT 1,
        value_col_offset        INT DEFAULT 0,
        range_axis              NVARCHAR(50) DEFAULT 'column',
        column_index            INT,
        column_letter           NVARCHAR(10),
        header_row              INT DEFAULT 0,
        stop_label_text         NVARCHAR(500),
        formula_expression      NVARCHAR(MAX),
        is_sales_amount         INT NOT NULL DEFAULT 0,
        sort_order              INT NOT NULL DEFAULT 0,
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        UNIQUE(supplier_profile_id, rule_name)
    );
END
GO

-- ---------------------------------------------------------------------------
-- SUPPLIER ALLOCATION RULES  (Tier C — pivot_allocation job_type)
-- Declares WHO each extracted total belongs to. member_id resolves against
-- the same member master every other supplier matches against.
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'supplier_allocation_rule')
BEGIN
    CREATE TABLE supplier_allocation_rule (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        supplier_profile_id     INT NOT NULL REFERENCES supplier_profile(id) ON DELETE CASCADE,
        extraction_rule_name    NVARCHAR(500) NOT NULL,
        member_id               INT NOT NULL REFERENCES member(id),
        allocation_type         NVARCHAR(50) NOT NULL DEFAULT 'percent',
        -- 'percent' | 'fixed_amount' | 'remainder' — see schema.sql for full docs
        allocation_value        FLOAT,
        contract_number         NVARCHAR(500),
        product_line            NVARCHAR(500),
        is_active               INT NOT NULL DEFAULT 1,
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        UNIQUE(supplier_profile_id, extraction_rule_name, member_id)
    );
END
GO

-- ---------------------------------------------------------------------------
-- MEMBER ALIASES
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'member_alias')
BEGIN
    CREATE TABLE member_alias (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        member_id               INT NOT NULL REFERENCES member(id) ON DELETE CASCADE,
        supplier_profile_id     INT REFERENCES supplier_profile(id) ON DELETE SET NULL,
        raw_name                NVARCHAR(MAX) NOT NULL,
        canonical_key           NVARCHAR(500) NOT NULL,
        jde_number              NVARCHAR(500),
        approved_by             NVARCHAR(500),
        approved_at             DATETIME2,
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        UNIQUE(supplier_profile_id, canonical_key)
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_member_alias_key')
    CREATE INDEX ix_member_alias_key ON member_alias(canonical_key);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_member_alias_jde')
    CREATE INDEX ix_member_alias_jde ON member_alias(jde_number);
GO

-- ---------------------------------------------------------------------------
-- PAYMENT LEDGER
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'payment_ledger')
BEGIN
    CREATE TABLE payment_ledger (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        payor                   NVARCHAR(500) NOT NULL,
        payment_received_date   DATE,
        deposit_amount          FLOAT NOT NULL,
        sf_payment_id           NVARCHAR(500) NOT NULL,
        contract_id             NVARCHAR(500),
        notes                   NVARCHAR(MAX),
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_ledger_payor')
    CREATE INDEX ix_ledger_payor ON payment_ledger(payor);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_ledger_deposit')
    CREATE INDEX ix_ledger_deposit ON payment_ledger(deposit_amount);
GO

-- ---------------------------------------------------------------------------
-- REVENUE IMPORT JOB
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'revenue_job')
BEGIN
    CREATE TABLE revenue_job (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        supplier_profile_id     INT NOT NULL REFERENCES supplier_profile(id),
        original_file_name      NVARCHAR(MAX) NOT NULL,
        file_path               NVARCHAR(MAX),
        status                  NVARCHAR(500) NOT NULL DEFAULT 'uploaded',
        payment_id              NVARCHAR(500),
        payment_id_source       NVARCHAR(500),
        deposit_amount          FLOAT,
        date_received           DATE,
        earned_period_start     DATE,
        earned_period_end       DATE,
        contract_id             NVARCHAR(500),
        row_count               INT,
        matched_count           INT,
        unmatched_count         INT,
        total_volume            FLOAT,
        total_revenue           FLOAT,
        error_message           NVARCHAR(MAX),
        column_mapping_json     NVARCHAR(MAX),
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        completed_at            DATETIME2
    );
END
GO

-- ---------------------------------------------------------------------------
-- REVENUE ROW
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'revenue_row')
BEGIN
    CREATE TABLE revenue_row (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        job_id                  INT NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
        row_number              INT NOT NULL,
        raw_row_json            NVARCHAR(MAX),
        jde_number              NVARCHAR(500),
        customer_name           NVARCHAR(MAX),
        process_date            DATE,
        city                    NVARCHAR(500),
        state                   NVARCHAR(500),
        zip                     NVARCHAR(500),
        address                 NVARCHAR(MAX),
        sales_amount            FLOAT,
        fee_amount              FLOAT,
        product_line            NVARCHAR(500),
        contract_number         NVARCHAR(500),
        matched_member_id       INT REFERENCES member(id),
        match_method            NVARCHAR(500),
        match_score             INT,
        match_geo_filter        NVARCHAR(500),
        entity_code             NVARCHAR(500),
        account_id              NVARCHAR(500),
        account_name_db         NVARCHAR(MAX),
        match_status            NVARCHAR(500) NOT NULL DEFAULT 'pending',
        validation_status       NVARCHAR(500) NOT NULL DEFAULT 'pending',
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_rev_row_job')
    CREATE INDEX ix_rev_row_job ON revenue_row(job_id);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_rev_row_jde')
    CREATE INDEX ix_rev_row_jde ON revenue_row(jde_number);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_rev_row_status')
    CREATE INDEX ix_rev_row_status ON revenue_row(match_status);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_rev_row_member')
    CREATE INDEX ix_rev_row_member ON revenue_row(matched_member_id);
GO

-- ---------------------------------------------------------------------------
-- MEMBER REVIEW ITEM
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'member_review_item')
BEGIN
    CREATE TABLE member_review_item (
        id                          INT IDENTITY(1,1) PRIMARY KEY,
        job_id                      INT NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
        jde_number                  NVARCHAR(500),
        customer_name_sample        NVARCHAR(MAX),
        canonical_key               NVARCHAR(500),
        city                        NVARCHAR(500),
        state                       NVARCHAR(500),
        zip                         NVARCHAR(500),
        suggested_member_id         INT REFERENCES member(id),
        suggested_match_score       INT,
        total_fee                   FLOAT,
        total_sales                 FLOAT,
        affected_row_count          INT,
        status                      NVARCHAR(500) NOT NULL DEFAULT 'pending',
        resolved_member_id          INT REFERENCES member(id),
        resolution_scope            NVARCHAR(500),
        resolved_by                 NVARCHAR(500),
        resolved_at                 DATETIME2,
        notes                       NVARCHAR(MAX),
        created_at                  DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_review_job')
    CREATE INDEX ix_review_job ON member_review_item(job_id);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_review_status')
    CREATE INDEX ix_review_status ON member_review_item(status);
GO

-- ---------------------------------------------------------------------------
-- REVENUE OUTPUT ROW
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'revenue_output_row')
BEGIN
    CREATE TABLE revenue_output_row (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        job_id                  INT NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
        pec                     NVARCHAR(500) NOT NULL,
        facility_id             NVARCHAR(500),
        contract_id             NVARCHAR(500),
        payment_id              NVARCHAR(500),
        volume                  FLOAT,
        total_revenue           FLOAT,
        revenue_type            NVARCHAR(500) NOT NULL DEFAULT 'Admin Fee',
        keep_return             NVARCHAR(500) NOT NULL DEFAULT 'Keep',
        earned_period           DATE,
        quarter_label           NVARCHAR(500),
        date_received           DATE,
        comments                NVARCHAR(MAX),
        date_refunded           DATE,
        rebate_retained         NVARCHAR(500),
        amount_retained         FLOAT,
        retained_reason         NVARCHAR(MAX),
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_output_job')
    CREATE INDEX ix_output_job ON revenue_output_row(job_id);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_output_pec')
    CREATE INDEX ix_output_pec ON revenue_output_row(pec);
GO

-- ---------------------------------------------------------------------------
-- EXCEPTION
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'exception')
BEGIN
    CREATE TABLE exception (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        job_id                  INT NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
        row_id                  INT REFERENCES revenue_row(id),
        exception_type          NVARCHAR(500) NOT NULL,
        message                 NVARCHAR(MAX) NOT NULL,
        severity                NVARCHAR(500) NOT NULL DEFAULT 'warning',
        status                  NVARCHAR(500) NOT NULL DEFAULT 'open',
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_exc_job')
    CREATE INDEX ix_exc_job ON exception(job_id);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_exc_type')
    CREATE INDEX ix_exc_type ON exception(exception_type);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_exc_severity')
    CREATE INDEX ix_exc_severity ON exception(severity);
GO

-- ---------------------------------------------------------------------------
-- EXPORT FILE
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'export_file')
BEGIN
    CREATE TABLE export_file (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        job_id                  INT NOT NULL REFERENCES revenue_job(id) ON DELETE CASCADE,
        artifact_type           NVARCHAR(500) NOT NULL,
        file_name               NVARCHAR(MAX) NOT NULL,
        file_path               NVARCHAR(MAX),
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE()
    );
END
GO

-- ---------------------------------------------------------------------------
-- JDE MAPPING (cross-job history)
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'jde_member_history')
BEGIN
    CREATE TABLE jde_member_history (
        id                      INT IDENTITY(1,1) PRIMARY KEY,
        supplier_profile_id     INT NOT NULL REFERENCES supplier_profile(id) ON DELETE CASCADE,
        jde_number              NVARCHAR(500) NOT NULL,
        member_id               INT NOT NULL REFERENCES member(id),
        first_seen_job_id       INT REFERENCES revenue_job(id),
        last_seen_job_id        INT REFERENCES revenue_job(id),
        occurrence_count        INT NOT NULL DEFAULT 1,
        created_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        updated_at              DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        UNIQUE(supplier_profile_id, jde_number)
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_jde_history_supplier')
    CREATE INDEX ix_jde_history_supplier ON jde_member_history(supplier_profile_id);
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'ix_jde_history_jde')
    CREATE INDEX ix_jde_history_jde ON jde_member_history(jde_number);
GO

-- ---------------------------------------------------------------------------
-- SCHEMA METADATA
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'schema_version')
BEGIN
    CREATE TABLE schema_version (
        version         INT PRIMARY KEY,
        applied_at      DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        description     NVARCHAR(MAX)
    );
END
GO

IF NOT EXISTS (SELECT * FROM schema_version WHERE version = 1)
BEGIN
    INSERT INTO schema_version(version, description)
    VALUES (1, 'Initial revenue processing engine schema');
END
GO
