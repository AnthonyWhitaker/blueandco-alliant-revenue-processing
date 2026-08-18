# Alliant Revenue Processing Engine

A **supplier-agnostic revenue reconciliation engine** for Alliant Purchasing Solutions. Extends Brandi's Guerbet prototype into a common platform that can process remittance files from any configured supplier.

---

## What It Does

1. **Reads** raw supplier remittance files (XLSX, CSV) — any format
2. **Maps** supplier-specific column names to canonical fields via a configurable column map
3. **Matches** each customer row to the Alliant member database using a 5-tier geographic + fuzzy name engine
4. **Aggregates** matched revenue to one row per entity code (PEC) per earned period
5. **Resolves** Salesforce Payment IDs automatically from the payment ledger
6. **Produces** a 4-tab Salesforce-ready XLSX (Upload, Validation, Unmatched, JDE Mapping)
7. **Queues** unmatched customers for human review — with fuzzy suggestions and alias creation

---

## Architecture

```
revenue_engine/
├── db/
│   └── schema.sql              Database schema (SQLite / Azure SQL compatible)
├── engine/
│   ├── constants.py            Canonical field names, thresholds, status values
│   ├── normalizer.py           Name/ZIP/state/city normalization utilities
│   ├── db.py                   Database connection and initialization
│   ├── member_loader.py        Load Account List.xlsx → member table
│   ├── member_matcher.py       5-tier geographic + fuzzy matching engine
│   ├── payment_resolver.py     Payment ID lookup from payment_ledger table
│   ├── file_reader.py          Read supplier files, apply column maps
│   ├── processor.py            Main orchestration pipeline (per job)
│   └── exporter.py             Generate 4-tab XLSX output
├── ui/
│   ├── app.py                  Streamlit app entry point + home dashboard
│   └── pages/
│       ├── 1_Upload.py         Upload supplier file, run processing
│       ├── 2_Review.py         Resolve unmatched customers
│       ├── 3_Export.py         Preview and download output files
│       └── 4_Admin.py          Manage suppliers, members, aliases, ledger
├── scripts/
│   └── seed_guerbet.py         Seeds Guerbet as supplier profile #1
├── data/                       Created at runtime — contains revenue.db
├── requirements.txt
└── README.md
```

---

## Database Schema

Adapted from `alliant-purchasing-solutions` (same architectural patterns, adapted for revenue):

| Table | Purpose | Equivalent in purchasing-solutions |
|---|---|---|
| `supplier_profile` | One row per supplier | `import_profile` |
| `supplier_column_map` | Column name → canonical field | `column_mapping` |
| `member` | Alliant member / facility master | `company` |
| `member_alias` | Alternate names for members | `company_alias` |
| `payment_ledger` | Deposit amount → SF Payment ID | *(new)* |
| `revenue_job` | One row per file processed | `import_job` |
| `revenue_row` | One row per line in supplier file | `import_row` |
| `member_review_item` | Unmatched customers needing review | `review_item` |
| `revenue_output_row` | Aggregated (PEC × month) output | *(new)* |
| `jde_member_history` | Persistent JDE→member mapping | *(new — replaces "previous file" pattern)* |
| `exception` | Processing warnings and errors | `exception` |
| `export_file` | Generated output file references | `export_file` |

---

## Getting Started

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Seed Guerbet (first supplier)

```bash
cd revenue_engine
python scripts/seed_guerbet.py \
    --account-list "../Uploads/Account List.xlsx" \
    --ledger "../Uploads/Ledger.xlsx"
```

### 3. Run the app

```bash
streamlit run ui/app.py
```

### 4. First-time setup in the UI

- **Admin → Members**: Load `Account List.xlsx` (if not done in step 2)
- **Admin → Payment Ledger**: Load `Ledger.xlsx` (if not done in step 2)
- **Admin → Suppliers**: Verify Guerbet profile is configured
- **Upload**: Select Guerbet, upload a raw Guerbet ledger file

---

## Matching Logic (5 Tiers)

The `MemberMatcher` class applies these tiers in order, stopping at the first successful match:

| Tier | Strategy | Threshold | Notes |
|---|---|---|---|
| 1 | **JDE History** | Exact | JDE Customer Number → persistent member mapping from prior jobs |
| 2 | **Alias Lookup** | Exact | Known alternate names (e.g., "OH MUHLENBERG LLC" → Owensboro Health) |
| 3 | **ZIP + State** | 75 | Fuzzy name within ZIP+State filtered candidates |
| 4 | **City + State** | 75 | Fuzzy name within City+State filtered candidates |
| 5 | **State Only** | 80 | Fuzzy name within State filtered candidates |
| 6 | **Nationwide** | 85 | Last resort — only for names > 5 characters |

Key difference from the Guerbet prototype: matched JDE numbers are **persisted to the database** (`jde_member_history` table), so future jobs get Tier 1 matches immediately for any previously-seen JDE number — across any number of months.

---

## Adding a New Supplier

### Via Admin UI

1. Go to **Admin → Suppliers** — click "Add Supplier"
2. Fill in: name, Salesforce contract ID, revenue type, payor filter name
3. Go to **Admin → Column Maps** — select the new supplier
4. Upload a sample file to **auto-infer column mappings**, then review and save
5. Adjust thresholds if needed (higher for less-reliable name data)

### Via seed script (copy `seed_guerbet.py`)

```python
conn.execute("INSERT INTO supplier_profile (name, contract_id, ...) VALUES (...)")
conn.execute("INSERT INTO supplier_column_map (supplier_profile_id, source_column, canonical_field) VALUES (...)")
```

---

## Output File Format

The downloaded XLSX has 4 tabs matching the Guerbet prototype:

| Tab | Contents |
|---|---|
| **Upload** | 16-column Salesforce Facility Revenue format — paste directly into SF |
| **Validation** | JDE# / supplier name / PEC / Account ID / DB name / match method |
| **Unmatched** | Raw rows with no member match (for manual investigation) |
| **JDE Mapping** | JDE# → PEC → DB name (cross-reference for future months) |

---

## Revenue Reconciliation Check

After aggregation, the engine checks:

```
|total_matched_revenue - deposit_amount| > $1.00  →  warning exception logged
```

This mirrors the 100% reconciliation requirement from the Guerbet prototype. Deposit amount is parsed from the filename (`Guerbet 2025.07.02 4231.37 Ledger.xlsx` → `$4,231.37`).

---

## Relationship to alliant-purchasing-solutions

This engine follows the same architectural patterns as the purchasing normalization app:

- **Import profiles** → Supplier profiles (supplier-specific config, not code)
- **Company normalization** → Member matching (geographic + fuzzy, not just name)
- **Review queue** → Unmatched customers (sorted by revenue impact, same as spend-weighted)
- **Alias table** → Member aliases (global or supplier-scoped)
- **Job lifecycle** → Same status flow: uploaded → processing → needs_review / completed
- **Exception tracking** → Same exception table structure

The DB schema is designed to be migrated to Azure SQL (the purchasing app's DB) in the future, running as a second set of tables in the same database if desired.

---

## Known Issues / Future Work

### Immediate
- [ ] The two recurring Guerbet unmatched customers (JDE 854488, 766089) need aliases linked in Admin → Aliases after Account List is loaded
- [ ] Payment ID column-based resolution (for suppliers that include it in the file) is implemented but untested

### Near-term
- [ ] Batch processing: watch a folder and auto-process new files
- [ ] Support for suppliers without JDE numbers (name-only matching)
- [ ] Medline support: item-level rows needing heavy rollup before matching

### Longer-term
- [ ] Azure SQL migration (schema is compatible)
- [ ] Azure Blob Storage for file storage (vs. local temp files)
- [ ] Teams integration: auto-detect new files in Alliant Receipts channel
- [ ] Direct Salesforce upload via API (vs. manual paste)
