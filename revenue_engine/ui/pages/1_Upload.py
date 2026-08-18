"""
Upload page — select supplier, upload raw file, run processing pipeline.
"""

import sys
import tempfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import init_db
from engine.processor import process_job

st.set_page_config(page_title="Upload | Revenue Processing", layout="wide")
st.title("📁 Upload & Process")

conn = st.session_state.get("conn") or init_db()

# Load supplier profiles
suppliers = conn.execute(
    "SELECT id, name, contract_id, revenue_type FROM supplier_profile ORDER BY name"
).fetchall()

if not suppliers:
    st.warning(
        "No supplier profiles configured. Go to **Admin → Suppliers** to add one "
        "(Guerbet is pre-seeded if you ran `scripts/seed_guerbet.py`)."
    )
    st.stop()

supplier_names = {s["name"]: dict(s) for s in suppliers}
selected_name = st.selectbox("Select Supplier", list(supplier_names.keys()))
selected_supplier = supplier_names[selected_name]

st.markdown(f"**Contract ID:** `{selected_supplier.get('contract_id') or '—'}`  |  "
            f"**Revenue Type:** `{selected_supplier.get('revenue_type', 'Admin Fee')}`")

st.divider()

uploaded_file = st.file_uploader(
    "Upload raw supplier file (XLSX or CSV)",
    type=["xlsx", "xls", "xlsm", "csv"],
)

col1, col2 = st.columns(2)
date_received = col1.date_input("Date Received (optional — parsed from filename if blank)")
manual_payment_id = col2.text_input("Payment ID Override (optional — auto-resolved from ledger)")

if uploaded_file and st.button("Process File", type="primary"):
    with st.spinner("Processing..."):
        # Save uploaded file to temp location
        suffix = Path(uploaded_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name

        try:
            # Create job record
            dr_str = str(date_received) if date_received else None
            cursor = conn.execute(
                """
                INSERT INTO revenue_job
                    (supplier_profile_id, original_file_name, file_path,
                     status, date_received, contract_id)
                VALUES (?, ?, ?, 'uploaded', ?, ?)
                """,
                (
                    selected_supplier["id"],
                    uploaded_file.name,
                    tmp_path,
                    dr_str,
                    selected_supplier.get("contract_id"),
                ),
            )
            conn.commit()
            job_id = cursor.lastrowid

            # Run pipeline
            result = process_job(
                conn=conn,
                job_id=job_id,
                manual_payment_id=manual_payment_id or None,
            )

            # Results summary
            status = result["status"]
            if status == "completed":
                st.success(f"Processing complete! Job #{job_id}")
            else:
                st.warning(f"Processing complete — {result['unmatched_count']} unmatched customers need review. Job #{job_id}")

            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("Total Rows", result["row_count"])
            col_b.metric("Matched", result["matched_count"])
            col_c.metric("Unmatched", result["unmatched_count"])
            col_d.metric(
                "Total Revenue",
                f"${result['total_revenue']:,.2f}" if result["total_revenue"] else "—",
            )

            if result.get("payment_id"):
                st.info(f"Payment ID: `{result['payment_id']}` (source: {result['payment_id_source']})")
            else:
                st.error(
                    "Payment ID could not be resolved. Enter it manually in the "
                    "Payment ID Override field and reprocess, or set it directly on the job."
                )

            if result["unmatched_count"] > 0:
                st.info("Go to the **Review** page to resolve unmatched customers.")

        except Exception as e:
            st.error(f"Processing failed: {e}")
            raise

st.divider()
st.subheader("Recent Jobs")

recent_jobs = conn.execute(
    """
    SELECT rj.id, sp.name AS supplier, rj.original_file_name,
           rj.status, rj.row_count, rj.matched_count, rj.unmatched_count,
           rj.total_revenue, rj.created_at
    FROM revenue_job rj
    JOIN supplier_profile sp ON sp.id = rj.supplier_profile_id
    ORDER BY rj.created_at DESC
    LIMIT 20
    """
).fetchall()

if recent_jobs:
    import pandas as pd
    df_jobs = pd.DataFrame([dict(r) for r in recent_jobs])
    df_jobs.columns = ["Job #", "Supplier", "File", "Status", "Rows", "Matched", "Unmatched", "Total Revenue", "Created"]
    st.dataframe(df_jobs, use_container_width=True, hide_index=True)
else:
    st.info("No jobs yet.")
