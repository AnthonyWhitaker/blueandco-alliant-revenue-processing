"""
SQL Server smoke check: runs the main flows through core/ against the database
named by AZURE_SQL_CONNECTION_STRING, using the synthetic pytest fixtures.
Catches SQLite-only SQL that the pytest suite (SQLite) can't see.

Run with `make check-mssql`, which starts a throwaway SQL Server container and
runs this inside the app image (it has the ODBC driver). Never point this at a
real database: it inserts test rows.
"""

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import jobs, members, misc, reviews, tier_c  # noqa: E402
from engine.db import init_db, is_mssql  # noqa: E402
from models.members import MemberInput  # noqa: E402
from models.reviews import ReviewApproval, ReviewNotMember  # noqa: E402
from engine.gpo_key import load_gpo_key  # noqa: E402
from engine.supplier_presets import MEDLINE, apply_preset  # noqa: E402
from tests import medline_mock  # noqa: E402
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
os.environ.setdefault("REVENUE_UPLOAD_DIR", tempfile.mkdtemp())
os.environ.setdefault("REVENUE_OUTPUT_DIR", tempfile.mkdtemp())
job = jobs.run_job(conn, jobs.create_job(conn, sid, [(FILE_NAME, src.read_bytes())], date(2025, 7, 2),
                                         timeframe="3Q25").id)
check("process job", (job.status, job.unmatched_count, job.total_revenue, job.payment_id)
      == ("needs_review", 1, 270.0, "PAY-1"), job.status)
check("reconciliation", job.reconciliation is not None and job.reconciliation.balanced and job.file_count == 1)
check("list jobs with LIMIT", [j.id for j in jobs.list_jobs(conn, has_pending_reviews=True, limit=5)] == [job.id])

item = reviews.list_pending_items(conn, job.id)[0]
check("review item built", (item.customer_name_sample, item.jde_number) == ("Zyxw Qvbn Clinic", "J300"))
check("review item candidates/reason columns", isinstance(item.candidates, list) and item.key_reason is None)
result = reviews.approve(conn, item.id, ReviewApproval(member_id=m["desert"], alias_scope="supplier"))
check("approve (alias + JDE history upsert)", result.job.status == "completed" and result.job.total_revenue == 300.0)
src_alias = conn.execute("SELECT source FROM member_alias WHERE raw_name = 'Zyxw Qvbn Clinic'").fetchone()[0]
check("alias source", src_alias == "Acme Supply", src_alias)
again = jobs.run_job(conn, jobs.create_job(conn, sid, [(FILE_NAME, src.read_bytes())], date(2025, 7, 2),
                                           timeframe="3Q25").id)
j300 = conn.execute("SELECT match_method FROM revenue_row WHERE job_id = ? AND jde_number = 'J300'",
                    (again.id,)).fetchone()[0]
check("same file again matches at the JDE step", (again.status, j300) == ("completed", "jde_history"), j300)

outputs = jobs.create_outputs(conn, job.id)
name, content = jobs.read_output(conn, job.id, outputs[0].id)
check("export", content[:2] == b"PK" and len(jobs.list_outputs(conn, job.id)) == 4, name)
check("member search", members.search_members(conn, term="mercy").total == 1)
check("ledger listing", members.list_ledger_entries(conn, limit=5).total == 1)

# Members without a Salesforce Account ID: several may exist (ux_member_account_id is filtered),
# and reloading an Account List with such rows updates them instead of adding them again.
members.create_member(conn, MemberInput(name="Walk-in Clinic One"))
members.create_member(conn, MemberInput(name="Walk-in Clinic Two"))
check("several members without an Account ID", True)
wb = openpyxl.Workbook()
wb.active.title = "Account List"
wb.active.append(["Account Name", "Account ID", "Premier Entity Code", "Billing State/Province"])
wb.active.append(["No ID Clinic A", None, "EC501", "TX"])
wb.active.append(["No ID Clinic B", None, "EC502", "TX"])
accounts = Path(tempfile.mkdtemp()) / "Account List.xlsx"
wb.save(accounts)
members.import_account_list(conn, str(accounts), replace_all=False)
members.import_account_list(conn, str(accounts), replace_all=False)
no_id = conn.execute("SELECT COUNT(*) FROM member WHERE name LIKE 'No ID Clinic %'").fetchone()[0]
check("Account List rows without an ID are not duplicated on reload", no_id == 2, no_id)

# Ship-to sites sharing a code, name and state stay separate members on reload.
wb = openpyxl.Workbook()
wb.active.title = "Account List"
wb.active.append(["Account Name", "Premier Entity Code", "Billing Street", "Billing State/Province",
                  "Billing Zip/Postal Code", "Is Primary"])
wb.active.append(["Site Clinic", "EC600", "1 Main Street", "TX", "78701", "Y"])
wb.active.append(["Site Clinic", "EC600", "2 Oak Avenue", "TX", "78701", "N"])
wb.save(accounts)
members.import_account_list(conn, str(accounts), replace_all=False)
members.import_account_list(conn, str(accounts), replace_all=False)
sites = conn.execute("SELECT COUNT(*) FROM member WHERE entity_code = 'EC600'").fetchone()[0]
check("ship-to sites not merged on reload", sites == 2, sites)
check("aliases", len(misc.list_aliases(conn)) == 1)
check("dashboard", misc.get_dashboard_stats(conn).pending_reviews == 0)

# "Not a member" is remembered for the supplier.
nid = make_supplier(conn, name="Acme Two", column_map=COLUMN_MAP)
first = jobs.run_job(conn, jobs.create_job(conn, nid, [(FILE_NAME, src.read_bytes())], date(2025, 7, 2)).id)
nm_item = reviews.list_pending_items(conn, first.id)[0]
reviews.mark_not_member(conn, nm_item.id, ReviewNotMember())
second = jobs.run_job(conn, jobs.create_job(conn, nid, [(FILE_NAME, src.read_bytes())], date(2025, 7, 2)).id)
status = conn.execute("SELECT match_status FROM revenue_row WHERE job_id = ? AND jde_number = 'J300'",
                      (second.id,)).fetchone()[0]
check("not a member remembered", (status, reviews.list_pending_items(conn, second.id)) == ("not_a_member", []))

# gpo_key_then_member: key failures go to review with the key's reason.
gid = apply_preset(conn, {**MEDLINE, "profile": {**MEDLINE["profile"], "identity_mode": "gpo_key_then_member"}})
tmp = Path(tempfile.mkdtemp())
load_gpo_key(conn, gid, str(medline_mock.write_key_csv(tmp / "key.csv")))
content = medline_mock.write_medline_xlsx(tmp / "m.xlsx").read_bytes()
gjob = jobs.run_job(conn, jobs.create_job(conn, gid, [("Medline 2026.10.15.xlsx", content)], date(2026, 10, 15),
                                          timeframe="3Q26").id)
key_reasons = sorted(i.key_reason for i in reviews.list_pending_items(conn, gjob.id))
check("gpo_key_then_member review items", key_reasons ==
      ["account_not_in_key", "address_mismatch", "missing_account_number", "name_mismatch"], key_reasons)

pid = make_supplier(conn, name="Komet", job_type="pivot_allocation")
rule = tier_c.save_extraction_rule(conn, pid, ExtractionRuleInput(
    extraction_method="named_cell", rule_name="Total", cell_address="E8")).rule
tier_c.save_allocation_rule(conn, pid, AllocationRuleInput(
    extraction_rule_name="Total", member_id=m["general"], allocation_type="remainder"))
renamed = tier_c.save_extraction_rule(conn, pid, ExtractionRuleInput(
    extraction_method="named_cell", rule_name="Grand", cell_address="E9"), rule_id=rule.id)
check("tier c rename cascade", renamed.renamed_allocation_refs == 1)
print("All SQL Server checks passed.")
