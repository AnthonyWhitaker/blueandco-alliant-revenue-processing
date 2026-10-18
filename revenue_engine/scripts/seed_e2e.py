"""
Create a fresh SQLite database with synthetic data for the Playwright suite
(frontend/e2e), plus the sample supplier file the tests upload.

    REVENUE_DB_PATH=/tmp/e2e/revenue.db python scripts/seed_e2e.py /tmp/e2e

Synthetic only (tests/fixtures.py): no real member or supplier data.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tier_c  # noqa: E402
from engine.db import init_db  # noqa: E402
from models.tier_c import AllocationRuleInput, ExtractionRuleInput  # noqa: E402
from tests.fixtures import COLUMN_MAP, FILE_NAME, make_members, make_supplier, write_supplier_file  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

conn = init_db()
make_supplier(conn, column_map=COLUMN_MAP)
members = make_members(conn)
# Lets the Acme file's Payment ID resolve from the ledger (payor, date, amount).
conn.execute(
    "INSERT INTO payment_ledger (payor, payment_received_date, deposit_amount, sf_payment_id) VALUES (?, ?, ?, ?)",
    ("Acme Supply", "2025-07-02", 300.00, "PAY-ACME-1"),
)
conn.commit()

pivot = make_supplier(conn, name="Pivot Demo", job_type="pivot_allocation")
tier_c.save_extraction_rule(conn, pivot, ExtractionRuleInput(
    extraction_method="named_cell", rule_name="Gross", sheet_name="Summary", cell_address="E8",
    is_sales_amount=True, sort_order=1,
))
tier_c.save_extraction_rule(conn, pivot, ExtractionRuleInput(
    extraction_method="label_lookup", rule_name="Fees Withheld", label_text="Fees",
    value_row_offset=0, value_col_offset=1, sort_order=2,
))
tier_c.save_allocation_rule(conn, pivot, AllocationRuleInput(
    extraction_rule_name="Gross", member_id=members["general"], allocation_type="percent", allocation_value=60,
))

write_supplier_file(out / FILE_NAME)
print(f"seeded {out}")
