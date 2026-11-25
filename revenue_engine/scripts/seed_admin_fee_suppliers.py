"""
Seed script: admin-fee suppliers defined in engine/supplier_presets.py (Medline).

    python scripts/seed_admin_fee_suppliers.py [--gpo-key "samples/medline/GPO Key (mock).csv"]
                                               [--include-placeholders]

--gpo-key loads a Medline GPO key file with the default column names
(engine/gpo_key.KeyColumns); a key without the ZIP column is loaded street-only.
Use scripts/load_gpo_key.py for other column names. A preset in gpo_key mode
is only switched to it once its key has loaded (engine/supplier_presets.seed_presets).
--include-placeholders also seeds the stub for the rolled-up supplier whose
sample hasn't arrived (it has placeholder column names, so its files can't be
processed until the column map is filled in).

Run from the revenue_engine/ directory. Exits with status 1 when the key file doesn't load.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from engine.supplier_presets import MEDLINE, PLACEHOLDER_PRESETS, PRESETS, seed_presets


def main():
    parser = argparse.ArgumentParser(description="Seed admin-fee suppliers")
    parser.add_argument("--db-path", default=None, help="SQLite DB path")
    parser.add_argument("--gpo-key", default=None, help="Medline GPO key file (CSV/XLSX) to load")
    parser.add_argument("--include-placeholders", action="store_true",
                        help="Also seed supplier stubs that still have placeholder column names")
    args = parser.parse_args()

    conn = init_db(args.db_path)
    presets = PRESETS + (PLACEHOLDER_PRESETS if args.include_placeholders else [])
    result = seed_presets(conn, presets, args.gpo_key, key_supplier=MEDLINE["profile"]["name"])
    for name, sid in result.supplier_ids.items():
        print(f"  Seeded '{name}' -> supplier_profile_id={sid}")
    if result.key_loaded is not None:
        street_only = " (no ZIP column: street only)" if result.key_without_zip else ""
        print(f"  Loaded {result.key_loaded} GPO key entries for Medline from {args.gpo_key}{street_only}")
    if result.key_error:
        print(f"  GPO key NOT loaded: {result.key_error}", file=sys.stderr)
    for name in result.held_back:
        print(f"  '{name}' is set to gpo_key in its preset but has no GPO key; left in its previous mode",
              file=sys.stderr)
    if result.key_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
