"""
Export page — preview and download processed output files.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import init_db
from engine.exporter import export_job, export_to_dataframes

st.set_page_config(page_title="Export | Revenue Processing", layout="wide")
st.title("📊 Export")

conn = st.session_state.get("conn") or init_db()

# Job selector
jobs = conn.execute(
    """
    SELECT rj.id, sp.name AS supplier, rj.original_file_name,
           rj.status, rj.total_revenue, rj.matched_count, rj.unmatched_count,
           rj.payment_id, rj.created_at
    FROM revenue_job rj
    JOIN supplier_profile sp ON sp.id = rj.supplier_profile_id
    WHERE rj.status IN ('completed', 'needs_review')
    ORDER BY rj.created_at DESC
    """
).fetchall()

if not jobs:
    st.info("No processed jobs available yet. Go to **Upload** to process a file.")
    st.stop()

job_options = {
    f"Job #{r['id']} — {r['supplier']} — {r['original_file_name']} [{r['status']}]": dict(r)
    for r in jobs
}
selected_label = st.selectbox("Select Job", list(job_options.keys()))
selected = job_options[selected_label]
job_id = selected["id"]

# Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Status", selected["status"].upper())
col2.metric("Matched Rows", selected["matched_count"] or "—")
col3.metric("Unmatched", selected["unmatched_count"] or "—")
col4.metric("Total Revenue", f"${selected['total_revenue']:,.2f}" if selected["total_revenue"] else "—")

if selected.get("payment_id"):
    st.info(f"Payment ID: `{selected['payment_id']}`")
else:
    manual_pid = st.text_input("Payment ID not set — enter it here before downloading:")
    if manual_pid:
        conn.execute(
            "UPDATE revenue_job SET payment_id = ? WHERE id = ?",
            (manual_pid, job_id),
        )
        conn.execute(
            "UPDATE revenue_output_row SET payment_id = ? WHERE job_id = ?",
            (manual_pid, job_id),
        )
        conn.commit()
        st.success("Payment ID saved.")

exceptions = conn.execute(
    "SELECT exception_type, message, severity, status, created_at FROM exception "
    "WHERE job_id = ? ORDER BY created_at",
    (job_id,),
).fetchall()
if exceptions:
    st.divider()
    n_errors = sum(1 for e in exceptions if e["severity"] == "error")
    n_warnings = sum(1 for e in exceptions if e["severity"] == "warning")
    label = f"⚠️ {len(exceptions)} exception(s) logged for this job"
    if n_errors:
        label += f" — {n_errors} error(s)"
    if n_warnings:
        label += f" — {n_warnings} warning(s)"
    with st.expander(label, expanded=(selected["status"] == "needs_review")):
        st.caption(
            "Review these before exporting/downloading, especially for "
            "aggregate/pivot-workpaper (Tier C) suppliers — a warning here "
            "can mean an extracted total or allocation could not be fully verified."
        )
        st.dataframe(
            pd.DataFrame([dict(e) for e in exceptions]),
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
            import tempfile, os
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                tmp_path = tmp.name
            output_path = export_job(conn, job_id, tmp_path)
            with open(output_path, "rb") as f:
                file_bytes = f.read()
            os.unlink(tmp_path)

        original_name = Path(selected["original_file_name"]).stem
        download_name = f"{original_name} Processed.xlsx"

        st.download_button(
            label=f"Download {download_name}",
            data=file_bytes,
            file_name=download_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        st.error(f"Export error: {e}")
