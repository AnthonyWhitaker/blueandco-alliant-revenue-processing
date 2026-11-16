"""
Load (replace) a supplier's GPO key from a CSV/XLSX key file.

    python scripts/load_gpo_key.py --supplier Medline --file "Medline GPO Key.xlsx" \
        [--account-number-column "Medline Account Number"] [--account-name-column "Medline Account Name"] \
        [--address-column "Medline Street"] [--gpo-id-column "Final GPO ID"] [--sheet NAME] [--header-row 0]

Column options default to engine/gpo_key.KeyColumns (the Medline Account Key headers). Without --sheet,
the first sheet that has all four columns is used.
The same import is available in the app (Admin > Suppliers) and at PUT /api/suppliers/{id}/gpo-key.
Run from the revenue_engine/ directory.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from engine.gpo_key import KeyColumns, load_gpo_key


def main():
    d = KeyColumns()
    parser = argparse.ArgumentParser(description="Load a supplier's GPO key")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--supplier", required=True, help="Supplier profile name, e.g. Medline")
    parser.add_argument("--file", required=True)
    parser.add_argument("--account-number-column", default=d.account_number)
    parser.add_argument("--account-name-column", default=d.account_name)
    parser.add_argument("--address-column", default=d.address)
    parser.add_argument("--gpo-id-column", default=d.gpo_id)
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--header-row", type=int, default=0)
    args = parser.parse_args()

    conn = init_db(args.db_path)
    row = conn.execute("SELECT id FROM supplier_profile WHERE name = ?", (args.supplier,)).fetchone()
    if not row:
        sys.exit(f"No supplier named {args.supplier!r}.")
    columns = KeyColumns(args.account_number_column, args.account_name_column, args.address_column,
                         args.gpo_id_column, args.sheet, args.header_row)
    result = load_gpo_key(conn, row["id"], args.file, columns)
    print(f"Loaded {result.loaded} entries ({result.skipped_blank_account} rows without an account number skipped; "
          f"{result.accounts_with_several_entries} accounts with several entries).")


if __name__ == "__main__":
    main()
