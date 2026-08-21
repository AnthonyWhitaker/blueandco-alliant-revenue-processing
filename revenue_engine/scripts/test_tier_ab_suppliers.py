"""
Runs the engine against every raw file for the newly-onboarded Tier A/B
suppliers (see seed_tier_ab_suppliers.py) and reports match rate + revenue
reconciliation vs. the deposit amount parsed from each filename.

There is no per-supplier "Processed.xlsx" ground truth for these suppliers
(unlike Guerbet) — Brandi's "2026 Complete" copies are mostly the raw file
plus scratch calculation tabs. This script is therefore a smoke test:
does the engine read the file, match customers, and reconcile to the
deposit amount — not a byte-for-byte comparison.

Run from revenue_engine/:
    python scripts/test_tier_ab_suppliers.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from engine.processor import process_job
from scripts.seed_guerbet import seed_guerbet
from scripts.seed_tier_ab_suppliers import SUPPLIERS, seed_supplier

ROOT = Path(__file__).parent.parent.parent
ACCOUNT_LIST = ROOT / "guerbet_example" / "Uploads" / "Account List.xlsx"
LEDGER = ROOT / "guerbet_example" / "Uploads" / "Ledger.xlsx"
RAW_DIR = ROOT / "2026 Original" / "2026 Complete"

# filename prefixes to associate with each supplier profile name
FILENAME_PREFIXES = {
    "Viscot": ["Viscot"],
    "Zier": ["Zier"],
    "Bon Secours": ["Bon Secours"],
    "Harbinger Medical": ["Harbinger"],
    "Propio": ["Propio"],
    "Electro Medical": ["Electro Medical"],
    "Medegen": ["Medegen"],
    "CME": ["CME"],
}


def find_files(prefixes: list[str]) -> list[Path]:
    out = []
    for f in RAW_DIR.iterdir():
        if f.suffix.lower() not in (".xlsx", ".xls", ".xlsm"):
            continue
        if "ledger" not in f.name.lower():
            continue  # skip ACH/bank-transaction files — not a remittance ledger
        if any(f.name.lower().startswith(p.lower()) for p in prefixes):
            out.append(f)
    return sorted(out)


def parse_deposit(filename: str):
    from engine.normalizer import parse_deposit_amount_from_filename
    return parse_deposit_amount_from_filename(filename)


def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="tier_ab_test_"))
    db_path = tmp_dir / "test.db"
    conn = init_db(str(db_path))

    print("Seeding Guerbet + members + ledger (shared across suppliers)...")
    seed_guerbet(conn, account_list_path=str(ACCOUNT_LIST), ledger_path=str(LEDGER))

    sp_ids = {}
    for definition in SUPPLIERS:
        name = definition["profile"]["name"]
        sp_ids[name] = seed_supplier(conn, definition)

    print()
    overall_pass = True

    try:
        for name, sp_id in sp_ids.items():
            files = find_files(FILENAME_PREFIXES.get(name, [name]))
            if not files:
                print(f"=== {name}: no raw files found, skipping ===")
                continue
            print(f"=== {name} ({len(files)} file(s)) ===")
            for f in files:
                dest = tmp_dir / f.name
                shutil.copy(f, dest)
                cursor = conn.execute(
                    """
                    INSERT INTO revenue_job (supplier_profile_id, original_file_name, file_path, status)
                    VALUES (?, ?, ?, 'uploaded')
                    """,
                    (sp_id, f.name, str(dest)),
                )
                conn.commit()
                job_id = cursor.lastrowid
                try:
                    result = process_job(conn, job_id)
                except Exception as e:
                    print(f"  [ERROR] {f.name}: {e}")
                    overall_pass = False
                    continue

                deposit = parse_deposit(f.name)
                match_rate = (
                    result["matched_count"] / result["row_count"] * 100
                    if result["row_count"] else 0
                )
                recon_ok = True
                recon_note = "n/a (no deposit amount in filename)"
                if deposit:
                    diff = abs((result["total_revenue"] or 0) - deposit)
                    recon_ok = diff < max(1.00, deposit * 0.02)
                    recon_note = f"deposit=${deposit:.2f} engine=${result['total_revenue']:.2f} diff=${diff:.2f}"

                status = "OK" if (match_rate > 0 and recon_ok) else "REVIEW"
                if status != "OK":
                    overall_pass = False
                print(
                    f"  [{status}] {f.name} — rows={result['row_count']} "
                    f"matched={result['matched_count']} ({match_rate:.0f}%) "
                    f"unmatched={result['unmatched_count']} — {recon_note}"
                )
            print()
    finally:
        conn.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("=" * 70)
    print("PASS" if overall_pass else "SOME FILES NEED REVIEW (see [REVIEW]/[ERROR] lines above)")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
