import sys, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from engine.processor import process_job
from engine.exporter import export_job
from scripts.seed_guerbet import seed_guerbet
from scripts.seed_tier_ab_suppliers import SUPPLIERS, seed_supplier

ROOT = Path(__file__).parent.parent.parent / "2026 Original" / "2026 Complete"
tmp = Path(tempfile.mkdtemp())
conn = init_db(str(tmp / "t.db"))
seed_guerbet(
    conn,
    account_list_path=str(ROOT.parent.parent / "guerbet_example" / "Uploads" / "Account List.xlsx"),
    ledger_path=str(ROOT.parent.parent / "guerbet_example" / "Uploads" / "Ledger.xlsx"),
)
for d in SUPPLIERS:
    if d["profile"]["name"] == "Carolon":
        sp_id = seed_supplier(conn, d)

grand_total = 0.0
for f in sorted(ROOT.glob("Carolon*Ledger*")):
    dest = tmp / f.name
    shutil.copy(f, dest)
    cur = conn.execute(
        "INSERT INTO revenue_job (supplier_profile_id, original_file_name, file_path, status) VALUES (?, ?, ?, 'uploaded')",
        (sp_id, f.name, str(dest)),
    )
    conn.commit()
    job_id = cur.lastrowid
    try:
        result = process_job(conn, job_id)
        grand_total += result["total_revenue"] or 0
        print(
            f.name,
            "rows", result["row_count"],
            "matched", result["matched_count"],
            "unmatched", result["unmatched_count"],
            "rev", round(result["total_revenue"], 2),
        )
    except Exception as e:
        print(f.name, "ERROR", e)

conn.close()
print(f"\nGrand total revenue: ${grand_total:.2f} (deposit = $1,219.79)")
diff = abs(grand_total - 1219.79)
print(f"Difference: ${diff:.2f}")
if diff < 1.00:
    print("PASS - reconciles within $1.00")
else:
    print("FAIL - does not reconcile")
shutil.rmtree(tmp, ignore_errors=True)
