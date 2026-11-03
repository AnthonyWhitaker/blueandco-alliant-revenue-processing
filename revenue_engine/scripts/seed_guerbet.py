"""
Seed script: Configures Guerbet as the first supplier profile in the database.
Also seeds the two recurring known aliases from guerbet_processor.py.

Run from the revenue_engine/ directory:
    python scripts/seed_guerbet.py

Optional flags:
    --db-path PATH         SQLite DB path (default: data/revenue.db)
    --account-list PATH    Load Account List.xlsx immediately
    --ledger PATH          Load Ledger.xlsx immediately
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db, execute_upsert, execute_insert_ignore, execute_select
from engine.normalizer import normalize_key

# ---------------------------------------------------------------------------
# Guerbet column map
# Maps Guerbet's raw XLSX column names → canonical engine field names.
# Covers both column name variations seen across months.
# ---------------------------------------------------------------------------
GUERBET_COLUMN_MAP = [
    # Process date
    ("Process_date",                "process_date",     True),
    ("process_date",                "process_date",     True),
    # Customer identifiers
    ("JDE Customer Number",         "jde_number",       True),
    ("Customer_Name",               "customer_name",    True),
    # Geography
    ("Address_Line_1",              "address",          False),
    ("City",                        "city",             False),
    ("State",                       "state",            False),
    ("ZIP Code",                    "zip",              False),
    # Revenue amounts
    ("Rebate_Net_Sales_Dollars",    "sales_amount",     False),
    ("Rebate Sales",                "sales_amount",     False),
    ("Admin Fee",                   "fee_amount",       True),
    ("Fee",                         "fee_amount",       True),
    # Metadata
    ("Product_Line_Desc",           "product_line",     False),
    ("Contract Number",             "contract_number",  False),
]

# ---------------------------------------------------------------------------
# Known aliases — hardcoded in guerbet_processor.py, now in DB
# ---------------------------------------------------------------------------
GUERBET_KNOWN_ALIASES = [
    # (raw_name, entity_code_hint, jde_number)
    # These must be linked to member IDs after Account List is loaded.
    # Stored here as raw names + JDE numbers so they can be resolved later.
    {
        "raw_name": "OH MUHLENBERG LLC",
        "jde_number": "854488",
        "target_entity_code": "KY0039",
        "notes": "Maps to Owensboro Health (KY0039)",
    },
    {
        "raw_name": "RESURGEONS ORTHOLINK",
        "jde_number": None,
        "target_entity_code": "656982",
        "notes": "Maps to United Musculoskeletal Partners - UMP (656982)",
    },
]


def seed_guerbet(
    conn,
    account_list_path: str = None,
    ledger_path: str = None,
) -> None:
    print("Seeding Guerbet supplier profile...")

    # Insert supplier profile
    execute_upsert(
        conn,
        """
        INSERT INTO supplier_profile (
            name, description, contract_id,
            revenue_type, keep_return_default,
            fuzzy_zip_state_threshold, fuzzy_city_state_threshold,
            fuzzy_state_threshold, fuzzy_nationwide_threshold,
            header_row, sheet_name,
            skip_rows_where_col_null,
            payment_id_source, payor_filter_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            description              = excluded.description,
            contract_id              = excluded.contract_id,
            fuzzy_zip_state_threshold = excluded.fuzzy_zip_state_threshold,
            payor_filter_name        = excluded.payor_filter_name,
            updated_at               = CURRENT_TIMESTAMP
        """,
        (
            "Guerbet",
            "Guerbet LLC — contrast media / imaging agents. Monthly remittance ledger.",
            "800o0000000LbmU",
            "Admin Fee",
            "Keep",
            75, 75, 80, 85,
            0, None,
            "JDE Customer Number",
            "ledger",
            "Guerbet",
        ),
    )
    conn.commit()

    sp_id = conn.execute("SELECT id FROM supplier_profile WHERE name = 'Guerbet'").fetchone()["id"]
    print(f"  Supplier profile ID: {sp_id}")

    # Insert column mappings
    inserted_maps = 0
    for source_col, canonical, required in GUERBET_COLUMN_MAP:
        try:
            execute_insert_ignore(
                conn,
                """
                INSERT OR IGNORE INTO supplier_column_map
                    (supplier_profile_id, source_column, canonical_field, is_required)
                VALUES (?, ?, ?, ?)
                """,
                (sp_id, source_col, canonical, 1 if required else 0),
            )
            inserted_maps += 1
        except Exception as e:
            print(f"  Warning: Could not insert column map '{source_col}': {e}")

    conn.commit()
    print(f"  Column mappings: {inserted_maps} inserted")

    # Load Account List if provided
    if account_list_path:
        from engine.member_loader import load_account_list
        print(f"  Loading Account List from: {account_list_path}")
        inserted, skipped, _ = load_account_list(conn, account_list_path)
        print(f"  Members: {inserted:,} loaded, {skipped} skipped")

    # Load Ledger if provided
    if ledger_path:
        from engine.payment_resolver import load_ledger_from_excel
        print(f"  Loading Ledger from: {ledger_path}")
        count = load_ledger_from_excel(conn, ledger_path)
        print(f"  Ledger: {count} entries loaded")

    # Seed known aliases (attempt to link by entity code / JDE if members are loaded)
    n_members = conn.execute("SELECT COUNT(*) FROM member").fetchone()[0]
    if n_members > 0:
        print("  Seeding known Guerbet aliases...")
        for alias_def in GUERBET_KNOWN_ALIASES:
            raw_name = alias_def["raw_name"]
            jde_num = alias_def.get("jde_number")
            target_entity_code = alias_def.get("target_entity_code")
            canon = normalize_key(raw_name)
            member_row = None
            if target_entity_code:
                member_row = execute_select(
                    conn,
                    "SELECT id FROM member WHERE entity_code = ? AND is_active = 1 LIMIT 1",
                    (target_entity_code,),
                ).fetchone()
            if not member_row:
                print(f"    Warning: no active member found for entity code "
                      f"'{target_entity_code}' — alias '{raw_name}' not linked")
                continue
            try:
                execute_insert_ignore(
                    conn,
                    """
                    INSERT OR IGNORE INTO member_alias
                        (member_id, supplier_profile_id, raw_name, canonical_key,
                         jde_number, approved_by, approved_at)
                    VALUES (?, ?, ?, ?, ?, 'seed_script', CURRENT_TIMESTAMP)
                    """,
                    (member_row["id"], sp_id, raw_name, canon, jde_num),
                )
            except Exception:
                pass
        conn.commit()
        print(f"  Known aliases seeded (link members in Admin > Aliases if needed)")
    else:
        print("  No members loaded yet -- run Admin > Members to load Account List first,")
        print("  then the known aliases will auto-link on first Review.")

    print("\nGuerbet seeding complete.")
    print(f"Supplier profile ID: {sp_id}")
    print("Next steps:")
    print("  1. Load Account List.xlsx via Admin > Members (or pass --account-list)")
    print("  2. Load Ledger.xlsx via Admin > Payment Ledger (or pass --ledger)")
    print("  3. Upload a Guerbet raw file via the Upload page")


def main():
    parser = argparse.ArgumentParser(description="Seed Guerbet supplier profile")
    parser.add_argument("--db-path", default=None, help="SQLite DB path")
    parser.add_argument("--account-list", default=None, help="Path to Account List.xlsx")
    parser.add_argument("--ledger", default=None, help="Path to Ledger.xlsx")
    args = parser.parse_args()

    conn = init_db(args.db_path)
    seed_guerbet(conn, account_list_path=args.account_list, ledger_path=args.ledger)


if __name__ == "__main__":
    main()
