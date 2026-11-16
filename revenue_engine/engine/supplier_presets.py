"""
Declarative supplier configurations for admin-fee suppliers.

Adding a supplier is a config change: a profile (supplier_profile columns)
plus a column map (raw header -> canonical field). apply_preset() writes one
to the database; scripts/seed_admin_fee_suppliers.py applies them all. The
same settings can be edited afterwards in Admin > Suppliers / Column Maps.

Profile keys not given fall back to the supplier_profile column defaults.
"""

from .db import execute_insert_ignore, execute_upsert

# Every supplier_profile column a preset may set.
_PROFILE_FIELDS = [
    "name", "description", "contract_id", "revenue_type", "keep_return_default",
    "header_row", "sheet_name", "skip_rows_where_col_null", "max_read_columns", "fee_rate",
    "payment_id_source", "payor_filter_name", "job_type", "requires_manual_confirmation",
    "identity_mode", "volume_mode",
]
_DEFAULTS = {
    "revenue_type": "Admin Fee", "keep_return_default": "Keep", "header_row": 0,
    "payment_id_source": "ledger", "job_type": "row_level", "requires_manual_confirmation": 0,
    "identity_mode": "member_match", "volume_mode": "sum",
}

MEDLINE = {
    "profile": dict(
        name="Medline",
        description="Medline — item-level invoice lines (one row per invoice line). GPO ID is verified against "
                    "the Medline GPO key by account number, account name and address; lines roll up to one "
                    "row per GPO ID per month (Volume = extended price, Total Revenue = GPO fee).",
        contract_id="AL-DS-001",
        payor_filter_name="Medline",
        # Only the grand-total row at the bottom has no invoice number. (Not the account number: an
        # invoice line missing its account number must reach the exceptions report, not be stripped.)
        skip_rows_where_col_null="InvoiceNumber",
        identity_mode="gpo_key",
        volume_mode="sum",
    ),
    # Headers of the mock file (scripts/make_medline_mock.py), modelled on Medline's 22-column file.
    # TODO(real data): confirm against a real Medline file.
    "column_map": [
        ("Medline Account #", "jde_number"),
        ("Account Name", "customer_name"),
        ("Account Street", "address"),
        ("Account City", "city"),
        ("Account State", "state"),
        ("Account Zip", "zip"),
        ("GPO ID", "entity_code_hint"),
        ("InvoiceDate", "process_date"),
        ("Extended Price Amount", "sales_amount"),
        ("GPO Fee Amount", "fee_amount"),
        ("ProductDivisionCode", "product_line"),
    ],
}

# TODO(real data): second supplier whose file is already rolled up by facility.
# Name, column headers and how it identifies a facility are placeholders until
# a sample arrives. It populates Total Revenue only; Volume stays blank.
ROLLED_UP_PLACEHOLDER = {
    "profile": dict(
        name="Rolled-up Supplier (placeholder)",
        description="PLACEHOLDER — file already rolled up by facility; populates Total Revenue only "
                    "(Volume blank). Replace the column map with the real headers when a sample arrives.",
        contract_id=None,                      # TODO(real data)
        payor_filter_name=None,                # TODO(real data): payor name as it appears on the ledger
        skip_rows_where_col_null="TODO Facility Name",
        identity_mode="member_match",
        volume_mode="blank",
    ),
    "column_map": [
        ("TODO Facility Name", "customer_name"),
        ("TODO Premier Entity Code", "entity_code_hint"),   # or account_id_hint for a Salesforce ID
        ("TODO City", "city"),
        ("TODO State", "state"),
        ("TODO Period", "process_date"),
        ("TODO Admin Fee", "fee_amount"),
    ],
}

PRESETS = [MEDLINE]
PLACEHOLDER_PRESETS = [ROLLED_UP_PLACEHOLDER]


def apply_preset(conn: object, preset: dict) -> int:
    """Create or update the supplier and add its column map entries. Returns supplier_profile_id."""
    profile = {**_DEFAULTS, **preset["profile"]}
    values = [profile.get(f) for f in _PROFILE_FIELDS]
    updates = ",\n            ".join(f"{f} = excluded.{f}" for f in _PROFILE_FIELDS if f != "name")
    execute_upsert(
        conn,
        f"""
        INSERT INTO supplier_profile ({", ".join(_PROFILE_FIELDS)})
        VALUES ({", ".join("?" for _ in _PROFILE_FIELDS)})
        ON CONFLICT(name) DO UPDATE SET
            {updates},
            updated_at = CURRENT_TIMESTAMP
        """,
        values,
    )
    conn.commit()
    supplier_id = conn.execute("SELECT id FROM supplier_profile WHERE name = ?", (profile["name"],)).fetchone()["id"]
    for source_col, canonical in preset["column_map"]:
        execute_insert_ignore(
            conn,
            "INSERT OR IGNORE INTO supplier_column_map (supplier_profile_id, source_column, canonical_field) "
            "VALUES (?, ?, ?)",
            (supplier_id, source_col, canonical),
        )
    conn.commit()
    return supplier_id
