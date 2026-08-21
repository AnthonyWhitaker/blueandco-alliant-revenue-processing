"""
Verifies the Tier C (pivot_allocation) engine end to end:
  1. Synthetic fixture covering every extraction_method (named_cell,
     label_lookup, label_range_sum, column_sum, formula) + allocation
     (percent split + remainder) through the real process_job() pipeline.
  2. Extraction-only check against a REAL Komet file to prove the parsing
     logic works on an actual pivot/summary workpaper (no fabricated
     allocation target is seeded to the real DB — that requires Brandi's
     confirmation of which facility/entity code the money belongs to,
     same as Bon Secours needed).
"""
import sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import openpyxl

from engine.db import init_db
from engine.processor import process_job
from engine.pivot_extractor import extract_totals

ROOT = Path(__file__).parent.parent.parent
tmp = Path(tempfile.mkdtemp())

# ---------------------------------------------------------------------
# 1. Build a synthetic fixture workbook covering every extraction method
# ---------------------------------------------------------------------
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Summary"
# named_cell target
ws["B2"] = "Region Total"
ws["E8"] = 1000.00
# label_lookup target (label then value one row below, same column)
ws["C10"] = "Grand Total"
ws["C11"] = 500.00
# label_range_sum target (label in row, sum column below until blank)
ws["D13"] = "Monthly Rebate"
ws["D14"] = 100.0
ws["D15"] = 150.0
ws["D16"] = 250.0
# column_sum target: header row 17 (0-based 16), values below in column F (index 5)
ws["A17"] = "Facility"
ws["F17"] = "Fee"
ws["F18"] = 10.0
ws["F19"] = 20.0
ws["F20"] = 30.0
fixture_path = tmp / "fixture.xlsx"
wb.save(fixture_path)

conn = init_db(str(tmp / "t.db"))
conn.execute(
    "INSERT INTO member (account_id, entity_code, alliant_entity_code, name, canonical_key, is_active) "
    "VALUES ('ACCT-A', 'EC-A', 'EC-A', 'Test Facility A', 'test facility a', 1)"
)
conn.execute(
    "INSERT INTO member (account_id, entity_code, alliant_entity_code, name, canonical_key, is_active) "
    "VALUES ('ACCT-B', 'EC-B', 'EC-B', 'Test Facility B', 'test facility b', 1)"
)
conn.commit()
mem_a = conn.execute("SELECT id FROM member WHERE account_id='ACCT-A'").fetchone()["id"]
mem_b = conn.execute("SELECT id FROM member WHERE account_id='ACCT-B'").fetchone()["id"]

cur = conn.execute(
    "INSERT INTO supplier_profile (name, job_type, requires_manual_confirmation, payor_filter_name) "
    "VALUES ('Test Tier C Supplier', 'pivot_allocation', 0, 'Test Tier C Supplier')"
)
conn.commit()
sp_id = cur.lastrowid

extraction_rules = [
    ("Region Total", "named_cell", dict(cell_address="E8")),
    ("Grand Total Fee", "label_lookup", dict(label_text="Grand Total", value_row_offset=1, value_col_offset=0)),
    ("Monthly Rebate Sum", "label_range_sum", dict(label_text="Monthly Rebate", range_axis="column")),
    ("Column Fee Sum", "column_sum", dict(column_letter="F", header_row=16)),
    ("Combined", "formula", dict(formula_expression="Region Total + Grand Total Fee")),
]
for name, method, kwargs in extraction_rules:
    cols = ", ".join(["supplier_profile_id", "rule_name", "extraction_method"] + list(kwargs.keys()))
    placeholders = ", ".join(["?"] * (3 + len(kwargs)))
    conn.execute(
        f"INSERT INTO supplier_extraction_rule ({cols}) VALUES ({placeholders})",
        (sp_id, name, method, *kwargs.values()),
    )
conn.commit()

# Allocation: "Region Total" split 60/40 between A and B; "Grand Total Fee",
# "Monthly Rebate Sum", "Column Fee Sum", "Combined" all go 100% to A.
conn.execute(
    "INSERT INTO supplier_allocation_rule (supplier_profile_id, extraction_rule_name, member_id, allocation_type, allocation_value) "
    "VALUES (?, 'Region Total', ?, 'percent', 60)",
    (sp_id, mem_a),
)
conn.execute(
    "INSERT INTO supplier_allocation_rule (supplier_profile_id, extraction_rule_name, member_id, allocation_type) "
    "VALUES (?, 'Region Total', ?, 'remainder')",
    (sp_id, mem_b),
)
for rule_name in ["Grand Total Fee", "Monthly Rebate Sum", "Column Fee Sum", "Combined"]:
    conn.execute(
        "INSERT INTO supplier_allocation_rule (supplier_profile_id, extraction_rule_name, member_id, allocation_type, allocation_value) "
        "VALUES (?, ?, ?, 'percent', 100)",
        (sp_id, rule_name, mem_a),
    )
conn.commit()

cur = conn.execute(
    "INSERT INTO revenue_job (supplier_profile_id, original_file_name, file_path, status) VALUES (?, ?, ?, 'uploaded')",
    (sp_id, "fixture.xlsx", str(fixture_path)),
)
conn.commit()
job_id = cur.lastrowid

result = process_job(conn, job_id)
print("Synthetic fixture result:", result)

expected = {
    "Region Total": 1000.0,       # 600 to A, 400 to B (remainder)
    "Grand Total Fee": 500.0,
    "Monthly Rebate Sum": 500.0,  # 100+150+250
    "Column Fee Sum": 60.0,       # 10+20+30
    "Combined": 1500.0,           # Region Total + Grand Total Fee
}
print("Extracted values:", result["extracted_values"])
assert result["extracted_values"] == expected, f"Mismatch: {result['extracted_values']} != {expected}"

rows = conn.execute(
    "SELECT customer_name, fee_amount, entity_code FROM revenue_row WHERE job_id=? ORDER BY row_number", (job_id,)
).fetchall()
for r in rows:
    print(dict(r))
total_expected = sum(expected.values())
assert abs(result["total_revenue"] - total_expected) < 0.01, (
    f"Total revenue {result['total_revenue']} != expected {total_expected}"
)
print(f"PASS — synthetic fixture reconciles: ${result['total_revenue']:.2f}")

conn.close()
shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------------
# 2. Extraction-only check against a REAL Komet file (no DB writes to
#    the real database — proves parsing works on an actual supplier
#    workpaper before any allocation target is confirmed with Brandi).
# ---------------------------------------------------------------------
print("\n--- Real-file extraction check: Komet ---")
tmp2 = Path(tempfile.mkdtemp())
conn2 = init_db(str(tmp2 / "t2.db"))
cur = conn2.execute(
    "INSERT INTO supplier_profile (name, job_type) VALUES ('Komet (extraction test only)', 'pivot_allocation')"
)
conn2.commit()
sp2_id = cur.lastrowid
conn2.execute(
    "INSERT INTO supplier_extraction_rule (supplier_profile_id, rule_name, sheet_name, extraction_method, label_text, value_row_offset, value_col_offset) "
    "VALUES (?, 'Rebate Total', 'Alliant Rebate Summary', 'label_lookup', 'Rebate $', 1, 0)",
    (sp2_id,),
)
conn2.commit()

komet_file = ROOT / "2026 Original" / "2026 Complete" / "Komet 2025.10.29 595.76 Ledger.xlsx"
result2 = extract_totals(str(komet_file), sp2_id, conn2)
print("Extracted:", result2.values, "warnings:", result2.warnings)
expected_komet = 595.7631
diff = abs(result2.values.get("Rebate Total", 0) - expected_komet)
print(f"Expected (from filename) ${expected_komet}, extracted ${result2.values.get('Rebate Total')}, diff ${diff:.4f}")
if diff < 0.01:
    print("PASS — real Komet pivot total extracted correctly")
else:
    print("FAIL — extraction did not match expected total")

conn2.close()
shutil.rmtree(tmp2, ignore_errors=True)
