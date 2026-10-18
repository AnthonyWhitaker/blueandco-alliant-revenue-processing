"""
Upload page — select supplier, upload raw file, run processing pipeline.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # revenue_engine/

from engine.db import init_db
from core.jobs import create_job, infer_from_filename, list_jobs, run_job
from core.suppliers import list_suppliers

st.set_page_config(page_title="Upload | Revenue Processing", layout="wide")
st.title("📁 Upload & Process")

conn = st.session_state.get("conn") or init_db()


def show_recent_jobs() -> None:
    st.subheader("Recent Jobs")
    recent_jobs = list_jobs(conn, limit=20)
    if recent_jobs:
        df_jobs = pd.DataFrame([
            {
                "Job #": j.id, "Supplier": j.supplier_name, "File": j.original_file_name,
                "Status": j.status, "Rows": j.row_count, "Matched": j.matched_count,
                "Unmatched": j.unmatched_count, "Total Revenue": j.total_revenue, "Created": j.created_at,
            }
            for j in recent_jobs
        ])
        st.dataframe(df_jobs, use_container_width=True, hide_index=True)
    else:
        st.info("No jobs yet.")


# Load supplier profiles
suppliers = list_suppliers(conn)

if not suppliers:
    st.warning(
        "No supplier profiles configured. Go to **Admin → Suppliers** to add one "
        "(Guerbet is pre-seeded if you ran `scripts/seed_guerbet.py`)."
    )
    st.stop()

supplier_names = {s.name: s for s in suppliers}

uploaded_file = st.file_uploader(
    "Upload raw supplier file (XLSX or CSV)",
    type=["xlsx", "xls", "xlsm", "csv"],
)

if not uploaded_file:
    st.info("Upload a file to continue — supplier and date received will be inferred from the filename.")
    st.divider()
    show_recent_jobs()
    st.stop()

# Infer supplier and date from the filename (only once per uploaded file)
if st.session_state.get("_upload_inferred_for") != uploaded_file.name:
    st.session_state["_upload_inferred_for"] = uploaded_file.name
    guess = infer_from_filename(conn, uploaded_file.name)
    st.session_state["_upload_guessed_supplier"] = guess.supplier_name
    st.session_state["_upload_guessed_date"] = guess.date_received

guessed_supplier = st.session_state.get("_upload_guessed_supplier")
guessed_date = st.session_state.get("_upload_guessed_date")

st.divider()

col1, col2 = st.columns(2)

supplier_options = ["— select —"] + list(supplier_names.keys())
default_idx = supplier_options.index(guessed_supplier) if guessed_supplier in supplier_options else 0
selected_name = col1.selectbox("Supplier", supplier_options, index=default_idx)
if guessed_supplier:
    col1.caption(f"Inferred from filename: **{guessed_supplier}**")
else:
    col1.caption("Could not infer supplier from filename — please select one.")

if guessed_date:
    date_received = col2.date_input("Date Received", value=guessed_date)
    col2.caption(f"Inferred from filename: **{guessed_date.isoformat()}**")
else:
    date_received = col2.date_input("Date Received", value=None)
    col2.caption("Could not infer date from filename — please select one.")

manual_payment_id = st.text_input("Payment ID Override (optional — auto-resolved from ledger)")

selected_supplier = supplier_names.get(selected_name)
if selected_supplier:
    st.markdown(f"**Contract ID:** `{selected_supplier.contract_id or '—'}`  |  "
                f"**Revenue Type:** `{selected_supplier.revenue_type}`")

ready_to_process = bool(uploaded_file and selected_supplier and date_received)

if not ready_to_process:
    st.caption("Select a supplier and date received to enable processing.")

if st.button("Process File", type="primary", disabled=not ready_to_process):
    with st.spinner("Processing..."):
        try:
            job = create_job(conn, selected_supplier.id, uploaded_file.name, uploaded_file.getvalue(), date_received)
            job = run_job(conn, job.id, manual_payment_id or None)

            # Results summary
            if job.status == "completed":
                st.success(f"Processing complete! Job #{job.id}")
            else:
                st.warning(f"Processing complete — {job.unmatched_count} unmatched customers need review. Job #{job.id}")

            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("Total Rows", job.row_count)
            col_b.metric("Matched", job.matched_count)
            col_c.metric("Unmatched", job.unmatched_count)
            col_d.metric(
                "Total Revenue",
                f"${job.total_revenue:,.2f}" if job.total_revenue else "—",
            )

            if job.payment_id:
                st.info(f"Payment ID: `{job.payment_id}` (source: {job.payment_id_source})")
            else:
                st.error(
                    "Payment ID could not be resolved. Enter it manually in the "
                    "Payment ID Override field and reprocess, or set it directly on the job."
                )

            if job.unmatched_count:
                st.info("Go to the **Review** page to resolve unmatched customers.")

        except Exception as e:
            st.error(f"Processing failed: {e}")

st.divider()
show_recent_jobs()
