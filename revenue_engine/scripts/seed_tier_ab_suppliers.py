"""
Seed script: configures the first batch of onboarded suppliers beyond Guerbet.

Tier A — supplier file already carries a Premier/Alliant Entity Code or a
         Salesforce Account ID per row (exact-match fast path, Tier 0 in
         MemberMatcher). Minimal risk of a wrong fuzzy match.
    - Viscot / Viscot Medical  (Premier Entity Code per row)
    - Zier / Zier Inc          (Salesforce Account ID per row — single lump-sum line)

Tier B — no entity code in the file; matched via the existing fuzzy
         name + geography engine (same mechanism as Guerbet).
    - Bon Secours       (has Entity_Code too -> gets the Tier 0 fast path)
    - Harbinger Medical (has GPO ID / Premier Entity Code -> Tier 0 fast path)
    - Propio            (has Facility_Premier_Entity_Code -> Tier 0 fast path)
    - Electro Medical   (name + city/state/zip only -> pure fuzzy match)
    - Medegen           (name + city/state only -> pure fuzzy match)

Run from the revenue_engine/ directory:
    python scripts/seed_tier_ab_suppliers.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db, execute_upsert, execute_insert_ignore

# ---------------------------------------------------------------------------
# Supplier definitions: (profile kwargs, column map list)
# Column map entries: (source_column, canonical_field, is_required)
# ---------------------------------------------------------------------------

SUPPLIERS = [
    {
        "profile": dict(
            name="Viscot",
            description="Viscot / Viscot Medical — surgical marking products. "
                        "Premier Entity Code supplied per row (Alliant template).",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Total Revenue",
            payor_filter_name="Viscot",
        ),
        "column_map": [
            ("Premier Entity Code", "entity_code_hint", False),
            ("Facility",            "customer_name",    True),
            ("Contract ID",         "contract_number",  False),
            ("Volume",              "sales_amount",     False),
            ("Total Revenue",       "fee_amount",       True),
            ("Earned Period",       "process_date",     True),
        ],
    },
    {
        "profile": dict(
            name="Zier",
            description="Zier / Zier Inc — Salesforce Account ID supplied directly "
                        "per row (Alliant template); typically one lump-sum line.",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Total Revenue",
            payor_filter_name="Zier",
        ),
        "column_map": [
            ("Premier Entity Code", "entity_code_hint", False),
            ("Facility",            "account_id_hint",  False),
            ("Contract ID",         "contract_number",  False),
            ("Volume",              "sales_amount",     False),
            ("Total Revenue",       "fee_amount",       True),
            ("Earned Period",       "process_date",     True),
        ],
    },
    {
        "profile": dict(
            name="Bon Secours",
            description="Bon Secours Mercy Health — supplier rebate ledger with "
                        "Alliant Entity Code supplied per row.",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Admin_Fee",
            payor_filter_name="Bon Secours",
        ),
        # Header names vary slightly month to month — both variants are mapped;
        # file_reader.py silently ignores whichever set isn't present in a given file.
        "column_map": [
            ("Entity_Code",           "entity_code_hint", False),
            ("Facility Entity Code",  "entity_code_hint", False),
            ("Facility_Name",         "customer_name",    True),
            ("Facility Name",         "customer_name",    True),
            ("St",                    "state",            False),
            ("State",                 "state",             False),
            ("Contract_ID",           "contract_number",  False),
            ("Contract ID",           "contract_number",  False),
            ("Earned_Period",         "process_date",     True),
            ("Earned Period",         "process_date",     True),
            ("Total Sales",           "sales_amount",     False),
            ("Sales",                 "sales_amount",     False),
            ("Admin_Fee",             "fee_amount",       True),
            ("Admin Fee",             "fee_amount",       True),
        ],
    },
    {
        "profile": dict(
            name="Harbinger Medical",
            description="Harbinger Medical — Premier Entity Code (as GPO ID) "
                        "supplied per row; earned period as quarter label (e.g. 'Q4 2025').",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Admin Fee",
            payor_filter_name="Harbinger",
        ),
        "column_map": [
            ("GPO ID / Premier Entity Code",   "entity_code_hint", False),
            ("Facility Name",                  "customer_name",    True),
            ("Facility State",                 "state",            False),
            ("Facility Zip",                   "zip",              False),
            ("Contract #",                     "contract_number",  False),
            ("Sales Volume",                   "sales_amount",     False),
            ("Admin Fee",                      "fee_amount",       True),
            ("Date of Sale / Earned Period",   "process_date",     True),
        ],
    },
    {
        "profile": dict(
            name="Propio",
            description="Propio Language Services — item-level invoice detail; "
                        "Facility Premier Entity Code supplied per row.",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Administration_Fee_Amount",
            payor_filter_name="Propio",
        ),
        "column_map": [
            ("Facility_Premier_Entity_Code", "entity_code_hint", False),
            ("Facility_Name",                "customer_name",    True),
            ("Facility_City",                "city",             False),
            ("Facility_State",               "state",            False),
            ("Facility_Zip_Code",            "zip",              False),
            ("Premier_Agreement_#",          "contract_number",  False),
            ("Invoice_Date",                 "process_date",     True),
            ("Total_Sales_Dollars",          "sales_amount",     False),
            ("Administration_Fee_Amount",    "fee_amount",       True),
        ],
    },
    {
        "profile": dict(
            name="Electro Medical",
            description="Electro Medical Systems — item-level invoice detail, "
                        "matched purely by facility name + city/state/zip.",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Administration_Fee_Amount",
            payor_filter_name="Electro Medical",
        ),
        "column_map": [
            ("Facility_Name",             "customer_name",   True),
            ("Facility_City",             "city",            False),
            ("Facility_State",            "state",           False),
            ("Facility_Zip_Code",         "zip",             False),
            ("Contract_#",                "contract_number", False),
            ("Invoice Date",              "process_date",    True),
            ("Total_Sales_Dollars",       "sales_amount",    False),
            ("Administration_Fee_Amount", "fee_amount",      True),
        ],
    },
    {
        "profile": dict(
            name="Medegen",
            description="Medegen — item-level invoice detail, matched purely "
                        "by member name + city/state.",
            contract_id=None,
            revenue_type="Admin Fee",
            keep_return_default="Keep",
            header_row=0,
            sheet_name=None,
            skip_rows_where_col_null="Admin Fee $",
            payor_filter_name="Medegen",
        ),
        "column_map": [
            ("Member Name",          "customer_name",   True),
            ("Member City",          "city",            False),
            ("Member State",         "state",           False),
            ("ROI Contract Number",  "contract_number", False),
            ("Period Start Date",    "process_date",    True),
            ("Sales",                "sales_amount",    False),
            ("Admin Fee $",          "fee_amount",       True),
        ],
    },
]


def seed_supplier(conn, definition: dict) -> int:
    p = definition["profile"]
    execute_upsert(
        conn,
        """
        INSERT INTO supplier_profile (
            name, description, contract_id,
            revenue_type, keep_return_default,
            header_row, sheet_name,
            skip_rows_where_col_null,
            payment_id_source, payor_filter_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ledger', ?)
        ON CONFLICT(name) DO UPDATE SET
            description               = excluded.description,
            contract_id               = excluded.contract_id,
            skip_rows_where_col_null  = excluded.skip_rows_where_col_null,
            payor_filter_name         = excluded.payor_filter_name,
            updated_at                = CURRENT_TIMESTAMP
        """,
        (
            p["name"], p["description"], p["contract_id"],
            p["revenue_type"], p["keep_return_default"],
            p["header_row"], p["sheet_name"],
            p["skip_rows_where_col_null"],
            p["payor_filter_name"],
        ),
    )
    conn.commit()
    sp_id = conn.execute(
        "SELECT id FROM supplier_profile WHERE name = ?", (p["name"],)
    ).fetchone()["id"]

    for source_col, canonical, required in definition["column_map"]:
        execute_insert_ignore(
            conn,
            """
            INSERT OR IGNORE INTO supplier_column_map
                (supplier_profile_id, source_column, canonical_field, is_required)
            VALUES (?, ?, ?, ?)
            """,
            (sp_id, source_col, canonical, 1 if required else 0),
        )
    conn.commit()
    return sp_id


def main():
    parser = argparse.ArgumentParser(description="Seed Tier A/B suppliers")
    parser.add_argument("--db-path", default=None, help="SQLite DB path")
    args = parser.parse_args()

    conn = init_db(args.db_path)
    for definition in SUPPLIERS:
        sp_id = seed_supplier(conn, definition)
        print(f"  Seeded '{definition['profile']['name']}' -> supplier_profile_id={sp_id}")

    print(f"\n{len(SUPPLIERS)} supplier profiles seeded.")
    print("Members and payment ledger are shared across all suppliers "
          "(load once via Admin > Members / Admin > Payment Ledger).")


if __name__ == "__main__":
    main()
