"""
Seed script: admin-fee suppliers defined in engine/supplier_presets.py (Medline).

    python scripts/seed_admin_fee_suppliers.py [--gpo-key "samples/medline/GPO Key (mock).csv"]
                                               [--include-placeholders]

--gpo-key loads a Medline GPO key file with the default column names
(engine/gpo_key.KeyColumns); use scripts/load_gpo_key.py for other column names.
--include-placeholders also seeds the stub for the rolled-up supplier whose
sample hasn't arrived (it has placeholder column names, so its files can't be
processed until the column map is filled in).

Run from the revenue_engine/ directory.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from engine.gpo_key import load_gpo_key
from engine.supplier_presets import MEDLINE, PLACEHOLDER_PRESETS, PRESETS, apply_preset


def main():
    parser = argparse.ArgumentParser(description="Seed admin-fee suppliers")
    parser.add_argument("--db-path", default=None, help="SQLite DB path")
    parser.add_argument("--gpo-key", default=None, help="Medline GPO key file (CSV/XLSX) to load")
    parser.add_argument("--include-placeholders", action="store_true",
                        help="Also seed supplier stubs that still have placeholder column names")
    args = parser.parse_args()

    conn = init_db(args.db_path)
    presets = PRESETS + (PLACEHOLDER_PRESETS if args.include_placeholders else [])
    ids = {}
    for preset in presets:
        ids[preset["profile"]["name"]] = apply_preset(conn, preset)
        print(f"  Seeded '{preset['profile']['name']}' -> supplier_profile_id={ids[preset['profile']['name']]}")
    if args.gpo_key:
        result = load_gpo_key(conn, ids[MEDLINE["profile"]["name"]], args.gpo_key)
        print(f"  Loaded {result.loaded} GPO key entries for Medline from {args.gpo_key}")


if __name__ == "__main__":
    main()
