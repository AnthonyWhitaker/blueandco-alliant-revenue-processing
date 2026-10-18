"""
Headless checks that the legacy Streamlit app still works on top of core/.
Remove together with ui/ once the React front end has replaced it.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from .conftest import make_supplier

UI = Path(__file__).resolve().parent.parent / "legacy" / "ui"
PAGES = ["app.py", "pages/1_Upload.py", "pages/2_Review.py", "pages/3_Export.py", "pages/4_Admin.py"]


def run(page: str) -> AppTest:
    at = AppTest.from_file(str(UI / page), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_on_empty_db(conn, page):
    run(page)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_a_processed_job(conn, processed_job, page):
    run(page)


def test_review_approve_suggestion_flow(conn, processed_job):
    at = run("pages/2_Review.py")
    item_id = conn.execute("SELECT id FROM member_review_item WHERE status = 'pending'").fetchone()[0]
    at.text_input(key=f"search_{item_id}").input("Desert").run()
    at.button(key=f"approve_{item_id}").click().run()
    assert not at.exception
    assert conn.execute("SELECT status FROM revenue_job").fetchone()[0] == "completed"
    assert at.success[0].value == "No pending reviews! All customers are matched."


def test_export_payment_id_saved_only_on_click(conn, processed_job):
    conn.execute("UPDATE revenue_job SET payment_id = NULL")
    conn.commit()
    at = run("pages/3_Export.py")
    at.text_input[0].input("PAY-MANUAL").run()
    assert conn.execute("SELECT payment_id FROM revenue_job").fetchone()[0] is None
    at.button[0].click().run()
    assert conn.execute("SELECT payment_id FROM revenue_job").fetchone()[0] == "PAY-MANUAL"


def test_tier_c_edit_prefills_and_preserves_fields(conn):
    """FINDINGS F13 regression: editing a rule used to wipe sheet/is_sales/sort_order."""
    sid = make_supplier(conn, name="AAA TierC", job_type="pivot_allocation")
    conn.execute(
        "INSERT INTO supplier_extraction_rule (supplier_profile_id, rule_name, sheet_name, extraction_method, "
        "cell_address, is_sales_amount, sort_order) VALUES (?, 'Beta', 'Summary', 'named_cell', 'B2', 1, 5)",
        (sid,),
    )
    conn.commit()
    rule_id = conn.execute("SELECT id FROM supplier_extraction_rule").fetchone()[0]
    at = run("pages/4_Admin.py")
    at.radio(key="er_mode").set_value("Edit existing").run()
    key = f"{sid}_{rule_id}"
    assert at.text_input(key=f"er_rule_name_{key}").value == "Beta"
    assert at.text_input(key=f"er_sheet_{key}").value == "Summary"
    at.text_input(key=f"er_cell_{key}").input("B9").run()
    at.button(key="er_save").click().run()
    assert not at.exception
    row = conn.execute(
        "SELECT cell_address, sheet_name, is_sales_amount, sort_order FROM supplier_extraction_rule"
    ).fetchone()
    assert tuple(row) == ("B9", "Summary", 1, 5)
