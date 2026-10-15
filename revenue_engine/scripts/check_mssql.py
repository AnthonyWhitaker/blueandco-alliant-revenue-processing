"""
SQL Server smoke check: runs the main flows through core/ against the database
named by AZURE_SQL_CONNECTION_STRING, using the synthetic pytest fixtures.
Catches SQLite-only SQL that the pytest suite (SQLite) can't see.

Run with `make check-mssql`, which starts a throwaway SQL Server container and
runs this inside the app image (it has the ODBC driver). Never point this at a
real database: it inserts test rows.
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import jobs, members, misc, reviews, tier_c  # noqa: E402
from engine.db import init_db, is_mssql  # noqa: E402
from models.reviews import ReviewApproval  # noqa: E402
from models.tier_c import AllocationRuleInput, ExtractionRuleInput  # noqa: E402
from tests.fixtures import COLUMN_MAP, FILE_NAME, make_members, make_supplier, write_supplier_file  # noqa: E402


def check(label: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
    if not ok:
        sys.exit(1)


assert is_mssql(), "AZURE_SQL_CONNECTION_STRING is not set"
conn = init_db()
sid = make_supplier(conn, column_map=COLUMN_MAP)
m = make_members(conn)
conn.execute(
    "INSERT INTO payment_ledger (payor, payment_received_date, deposit_amount, sf_payment_id) VALUES (?, ?, ?, ?)",
    ("Acme Supply", "2025-07-02", 300.0, "PAY-1"),
)
conn.commit()

src = write_supplier_file(Path(tempfile.mkdtemp()) / "src.xlsx")
job = jobs.run_job(conn, jobs.create_job(conn, sid, FILE_NAME, src.read_bytes(), date(2025, 7, 2)).id)
check("process job", (job.status, job.unmatched_count, job.total_revenue, job.payment_id)
      == ("needs_review", 1, 270.0, "PAY-1"), job.status)
check("list jobs with LIMIT", [j.id for j in jobs.list_jobs(conn, has_pending_reviews=True, limit=5)] == [job.id])

item = reviews.list_pending_items(conn, job.id)[0]
check("review item built", (item.customer_name_sample, item.jde_number) == ("Zyxw Qvbn Clinic", "J300"))
result = reviews.approve(conn, item.id, ReviewApproval(member_id=m["desert"], alias_scope="supplier"))
check("approve (alias + JDE history upsert)", result.job.status == "completed" and result.job.total_revenue == 300.0)

name, content = jobs.build_export(conn, job.id)
check("export", content[:2] == b"PK", name)
check("member search", members.search_members(conn, term="mercy").total == 1)
check("ledger listing", members.list_ledger_entries(conn, limit=5).total == 1)
check("aliases", len(misc.list_aliases(conn)) == 1)
check("dashboard", misc.get_dashboard_stats(conn).pending_reviews == 0)

pid = make_supplier(conn, name="Komet", job_type="pivot_allocation")
rule = tier_c.save_extraction_rule(conn, pid, ExtractionRuleInput(
    extraction_method="named_cell", rule_name="Total", cell_address="E8")).rule
tier_c.save_allocation_rule(conn, pid, AllocationRuleInput(
    extraction_rule_name="Total", member_id=m["general"], allocation_type="remainder"))
renamed = tier_c.save_extraction_rule(conn, pid, ExtractionRuleInput(
    extraction_method="named_cell", rule_name="Grand", cell_address="E9"), rule_id=rule.id)
check("tier c rename cascade", renamed.renamed_allocation_refs == 1)
print("All SQL Server checks passed.")
