"""
Upload page — select supplier, upload raw file, run processing pipeline.
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import init_db, get_last_insert_id
from engine.processor import process_job
from engine.normalizer import parse_date_from_filename, guess_supplier_from_filename

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

uploaded_file = st.file_uploader(
    "Upload raw supplier file (XLSX or CSV)",
    type=["xlsx", "xls", "xlsm", "csv"],
)

if not uploaded_file:
    st.info("Upload a file to continue — supplier and date received will be inferred from the filename.")
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
    st.stop()

# Infer supplier and date from the filename (only once per uploaded file)
if st.session_state.get("_upload_inferred_for") != uploaded_file.name:
    st.session_state["_upload_inferred_for"] = uploaded_file.name
    st.session_state["_upload_guessed_supplier"] = guess_supplier_from_filename(
        uploaded_file.name, list(supplier_names.keys())
    )
    st.session_state["_upload_guessed_date"] = parse_date_from_filename(uploaded_file.name)

guessed_supplier = st.session_state.get("_upload_guessed_supplier")
guessed_date_str = st.session_state.get("_upload_guessed_date")

st.divider()

col1, col2 = st.columns(2)

supplier_options = ["— select —"] + list(supplier_names.keys())
default_idx = supplier_options.index(guessed_supplier) if guessed_supplier in supplier_options else 0
selected_name = col1.selectbox("Supplier", supplier_options, index=default_idx)
if guessed_supplier:
    col1.caption(f"Inferred from filename: **{guessed_supplier}**")
else:
    col1.caption("Could not infer supplier from filename — please select one.")

if guessed_date_str:
    guessed_date = datetime.strptime(guessed_date_str, "%Y-%m-%d").date()
    date_received = col2.date_input("Date Received", value=guessed_date)
    col2.caption(f"Inferred from filename: **{guessed_date_str}**")
else:
    date_received = col2.date_input("Date Received", value=None)
    col2.caption("Could not infer date from filename — please select one.")

manual_payment_id = st.text_input("Payment ID Override (optional — auto-resolved from ledger)")

selected_supplier = supplier_names.get(selected_name)
if selected_supplier:
    st.markdown(f"**Contract ID:** `{selected_supplier.get('contract_id') or '—'}`  |  "
                f"**Revenue Type:** `{selected_supplier.get('revenue_type', 'Admin Fee')}`")

ready_to_process = bool(uploaded_file and selected_supplier and date_received)

if not ready_to_process:
    st.caption("Select a supplier and date received to enable processing.")

if st.button("Process File", type="primary", disabled=not ready_to_process):
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
            job_id = get_last_insert_id(conn, cursor)

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
