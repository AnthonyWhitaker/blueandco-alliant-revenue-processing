"""
Regression test: runs the engine against the raw Guerbet 2025.10.21 ledger
and compares the result to Brandi's already-processed "Ledger Processed.xlsx"
(the ground-truth pair supplied in 2026 Original / 2026 Complete).

Does NOT touch the real revenue.db — uses a throwaway SQLite file.

Run from revenue_engine/:
    python scripts/regression_test_guerbet.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import openpyxl

from engine.db import init_db
from engine.processor import process_job
from engine.exporter import export_job
from scripts.seed_guerbet import seed_guerbet

ROOT = Path(__file__).parent.parent.parent  # alliant-revenue-processing/
ACCOUNT_LIST = ROOT / "guerbet_example" / "Uploads" / "Account List.xlsx"
LEDGER = ROOT / "guerbet_example" / "Uploads" / "Ledger.xlsx"
RAW_FILE = ROOT / "2026 Original" / "2026 Complete" / "Guerbet 2025.10.21 4248.85 Ledger.xlsx"
TRUTH_FILE = ROOT / "2026 Complete" / "2026 Completed" / "Guerbet 2025.10.21 4248.85 Ledger Processed.xlsx"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def load_pec_totals_from_upload_tab(path: Path, sheet: str, pec_col: str, revenue_col: str) -> dict[str, float]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    pec_idx = header.index(pec_col)
    rev_idx = header.index(revenue_col)
    totals: dict[str, float] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        pec = row[pec_idx]
        rev = row[rev_idx]
        if pec is None or rev is None:
            continue
        totals[str(pec)] = totals.get(str(pec), 0.0) + float(rev)
    return totals


def main() -> int:
    print("=" * 70)
    print("Guerbet 2025.10.21 regression test (raw -> engine vs. Brandi's truth)")
    print("=" * 70)

    if not RAW_FILE.exists() or not TRUTH_FILE.exists():
        print(f"Missing input files:\n  raw={RAW_FILE}\n  truth={TRUTH_FILE}")
        return 1

    tmp_dir = Path(tempfile.mkdtemp(prefix="guerbet_regression_"))
    db_path = tmp_dir / "regression.db"
    raw_copy = tmp_dir / RAW_FILE.name
    shutil.copy(RAW_FILE, raw_copy)

    try:
        conn = init_db(str(db_path))
        seed_guerbet(conn, account_list_path=str(ACCOUNT_LIST), ledger_path=str(LEDGER))
        sp_id = conn.execute("SELECT id FROM supplier_profile WHERE name = 'Guerbet'").fetchone()["id"]

        cursor = conn.execute(
            """
            INSERT INTO revenue_job (supplier_profile_id, original_file_name, file_path, status)
            VALUES (?, ?, ?, 'uploaded')
            """,
            (sp_id, RAW_FILE.name, str(raw_copy)),
        )
        conn.commit()
        job_id = cursor.lastrowid

        result = process_job(conn, job_id)
        print(f"\nEngine result: {result}\n")

        output_path = export_job(conn, job_id, output_path=str(tmp_dir / "engine_output.xlsx"))

        # -----------------------------------------------------------------
        # Compare: total revenue vs. deposit amount in filename ($4,248.85)
        # -----------------------------------------------------------------
        check(
            "Total matched revenue reconciles to deposit amount",
            abs(result["total_revenue"] - 4248.85) < 5.00,
            f"engine total_revenue={result['total_revenue']}",
        )

        # -----------------------------------------------------------------
        # Compare: engine unmatched count vs. Brandi's Unmatched tab row count
        # -----------------------------------------------------------------
        truth_wb = openpyxl.load_workbook(TRUTH_FILE, data_only=True)
        truth_unmatched_rows = truth_wb["Unmatched"].max_row - 1  # minus header
        check(
            "Unmatched row count is same order of magnitude as truth file",
            result["unmatched_count"] <= truth_unmatched_rows + 5,
            f"engine unmatched={result['unmatched_count']} truth unmatched={truth_unmatched_rows}",
        )

        # -----------------------------------------------------------------
        # Compare: per-PEC total revenue, engine Upload tab vs. truth Upload tab
        # -----------------------------------------------------------------
        engine_totals = load_pec_totals_from_upload_tab(Path(output_path), "Upload", "Pec", "Total Revenue")
        truth_totals = load_pec_totals_from_upload_tab(TRUTH_FILE, "Upload", "Pec", "Total Revenue")

        common_pecs = set(engine_totals) & set(truth_totals)
        check(
            "At least 80% of Brandi's matched PECs are also matched by the engine",
            len(common_pecs) >= 0.8 * len(truth_totals),
            f"common={len(common_pecs)} truth_total_pecs={len(truth_totals)}",
        )

        mismatches = []
        for pec in sorted(common_pecs):
            diff = abs(engine_totals[pec] - truth_totals[pec])
            if diff > 0.05:
                mismatches.append((pec, engine_totals[pec], truth_totals[pec], diff))
        check(
            "Per-PEC revenue amounts match (within $0.05) for all commonly-matched PECs",
            len(mismatches) == 0,
            f"{len(mismatches)} mismatches, e.g. {mismatches[:5]}",
        )

        missing_pecs = set(truth_totals) - set(engine_totals)
        if missing_pecs:
            print(f"\n  PECs Brandi matched that the engine did NOT match: {sorted(missing_pecs)[:10]}"
                  f"{' ...' if len(missing_pecs) > 10 else ''}")

    finally:
        try:
            conn.close()
        except Exception:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"REGRESSION TEST FAILED — {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1

    print("REGRESSION TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
