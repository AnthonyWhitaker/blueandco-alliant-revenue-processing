"""
Export page — preview and download processed output files.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # revenue_engine/

from engine.db import init_db
from engine.exporter import export_to_dataframes
from core.jobs import EXPORTABLE_STATUSES, build_export, confirm_job, list_exceptions, list_jobs, set_payment_id

st.set_page_config(page_title="Export | Revenue Processing", layout="wide")
st.title("📊 Export")

conn = st.session_state.get("conn") or init_db()

# Job selector
jobs = list_jobs(conn, statuses=EXPORTABLE_STATUSES)

if not jobs:
    st.info("No processed jobs available yet. Go to **Upload** to process a file.")
    st.stop()

job_options = {
    f"Job #{j.id} — {j.supplier_name} — {j.original_file_name} [{j.status}]": j
    for j in jobs
}
selected_label = st.selectbox("Select Job", list(job_options.keys()))
selected = job_options[selected_label]
job_id = selected.id

# Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Status", selected.status.upper())
col2.metric("Matched Rows", selected.matched_count or "—")
col3.metric("Unmatched", selected.unmatched_count or "—")
col4.metric("Total Revenue", f"${selected.total_revenue:,.2f}" if selected.total_revenue else "—")

if selected.payment_id:
    st.info(f"Payment ID: `{selected.payment_id}`")
else:
    pid_col, btn_col = st.columns([3, 1], vertical_alignment="bottom")
    manual_pid = pid_col.text_input("Payment ID not set — enter it here before downloading:")
    if btn_col.button("Save Payment ID", disabled=not manual_pid.strip()):
        set_payment_id(conn, job_id, manual_pid.strip())
        st.success("Payment ID saved.")
        st.rerun()

# A needs_review job with nothing left in the review queue (e.g. a Tier C job,
# or one whose remaining items were dismissed) can be signed off here.
if selected.status == "needs_review" and selected.pending_review_count == 0:
    st.warning("This job needs sign-off: there is nothing left to review, but it was flagged for confirmation. "
               "Check the exceptions below, then confirm.")
    if st.button("Confirm job as complete"):
        confirm_job(conn, job_id)
        st.rerun()

exceptions = list_exceptions(conn, job_id)
if exceptions:
    st.divider()
    n_errors = sum(1 for e in exceptions if e.severity == "error")
    n_warnings = sum(1 for e in exceptions if e.severity == "warning")
    label = f"⚠️ {len(exceptions)} exception(s) logged for this job"
    if n_errors:
        label += f" — {n_errors} error(s)"
    if n_warnings:
        label += f" — {n_warnings} warning(s)"
    with st.expander(label, expanded=(selected.status == "needs_review")):
        st.caption(
            "Review these before exporting/downloading, especially for "
            "aggregate/pivot-workpaper (Tier C) suppliers — a warning here "
            "can mean an extracted total or allocation could not be fully verified."
        )
        st.dataframe(
            pd.DataFrame([e.model_dump(exclude={"id"}) for e in exceptions]),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# Preview tabs
st.subheader("Output Preview")
try:
    dfs = export_to_dataframes(conn, job_id)
    tab_upload, tab_validation, tab_unmatched, tab_jde = st.tabs(
        ["Upload", "Validation", "Unmatched", "JDE Mapping"]
    )
    with tab_upload:
        st.caption(f"{len(dfs['Upload'])} rows — Salesforce Facility Revenue upload format")
        st.dataframe(dfs["Upload"], use_container_width=True, hide_index=True)
    with tab_validation:
        st.caption(f"{len(dfs['Validation'])} unique matched customers")
        st.dataframe(dfs["Validation"], use_container_width=True, hide_index=True)
    with tab_unmatched:
        n = len(dfs["Unmatched"])
        if n > 0:
            st.warning(f"{n} unmatched row(s) — go to Review page to resolve")
        st.dataframe(dfs["Unmatched"], use_container_width=True, hide_index=True)
    with tab_jde:
        st.caption(f"{len(dfs['JDE Mapping'])} JDE number mappings")
        st.dataframe(dfs["JDE Mapping"], use_container_width=True, hide_index=True)
except Exception as e:
    st.error(f"Preview error: {e}")

st.divider()
st.subheader("Download")

# Generate XLSX in memory for download
if st.button("Generate Download File", type="primary"):
    try:
        with st.spinner("Building XLSX…"):
            download_name, file_bytes = build_export(conn, job_id)

        st.download_button(
            label=f"Download {download_name}",
            data=file_bytes,
            file_name=download_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        st.error(f"Export error: {e}")
