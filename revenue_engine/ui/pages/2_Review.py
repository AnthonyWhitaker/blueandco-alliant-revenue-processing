"""
Review page — resolve unmatched customers from processed jobs.

For each unmatched customer group the engine shows:
  - The raw customer name, JDE number, city/state/zip
  - The best fuzzy match suggestion (with score)
  - Buttons: Approve suggestion | Search for different member | Dismiss (no match)
  - Option to create an alias so future jobs auto-match
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import init_db
from engine.processor import apply_review_decision
from engine.member_loader import create_member

st.set_page_config(page_title="Review | Revenue Processing", layout="wide")
st.title("🔍 Review — Unmatched Customers")

conn = st.session_state.get("conn") or init_db()

# Filter by job
jobs_with_pending = conn.execute(
    """
    SELECT DISTINCT rj.id, rj.original_file_name, sp.name AS supplier,
           COUNT(mri.id) AS pending_count
    FROM member_review_item mri
    JOIN revenue_job rj ON rj.id = mri.job_id
    JOIN supplier_profile sp ON sp.id = rj.supplier_profile_id
    WHERE mri.status = 'pending'
    GROUP BY rj.id
    ORDER BY rj.created_at DESC
    """
).fetchall()

if not jobs_with_pending:
    st.success("No pending reviews! All customers are matched.")
    st.stop()

job_options = {
    f"Job #{r['id']} — {r['supplier']} — {r['original_file_name']} ({r['pending_count']} pending)": r["id"]
    for r in jobs_with_pending
}
selected_label = st.selectbox("Select Job to Review", list(job_options.keys()))
selected_job_id = job_options[selected_label]

job_row = conn.execute("SELECT * FROM revenue_job WHERE id = ?", (selected_job_id,)).fetchone()
job_row = dict(job_row)

st.divider()

# Load pending review items for this job
review_items = conn.execute(
    """
    SELECT mri.*,
           m.name AS suggested_name, m.entity_code AS suggested_entity_code,
           m.billing_city AS suggested_city, m.billing_state AS suggested_state
    FROM member_review_item mri
    LEFT JOIN member m ON m.id = mri.suggested_member_id
    WHERE mri.job_id = ? AND mri.status = 'pending'
    ORDER BY mri.total_fee DESC
    """,
    (selected_job_id,),
).fetchall()

st.markdown(f"### {len(review_items)} unmatched customer(s) — sorted by revenue impact")
st.caption("Resolve each one below. Approved matches update the output file automatically.")

for item in review_items:
    item = dict(item)
    with st.expander(
        f"**{item['customer_name_sample']}**  —  "
        f"JDE: `{item.get('jde_number') or '—'}`  |  "
        f"Fee: `${item['total_fee']:.2f}`  |  "
        f"{item['affected_row_count']} row(s)",
        expanded=True,
    ):
        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("**Unmatched Customer**")
            st.markdown(f"- **Name:** {item['customer_name_sample']}")
            st.markdown(f"- **JDE #:** {item.get('jde_number') or '—'}")
            st.markdown(f"- **Location:** {item.get('city') or ''}, {item.get('state') or ''} {item.get('zip') or ''}")
            st.markdown(f"- **Fee Amount:** ${item['total_fee']:,.2f}")
            st.markdown(f"- **Rows Affected:** {item['affected_row_count']}")

        with col_right:
            if item.get("suggested_member_id"):
                st.markdown("**Best Suggestion**")
                st.markdown(f"- **Name:** {item.get('suggested_name', '—')}")
                st.markdown(f"- **Entity Code:** `{item.get('suggested_entity_code', '—')}`")
                st.markdown(f"- **Location:** {item.get('suggested_city') or ''}, {item.get('suggested_state') or ''}")
                score = item.get("suggested_match_score", 0)
                color = "green" if score >= 80 else ("orange" if score >= 60 else "red")
                st.markdown(f"- **Match Score:** :{color}[{score}]")
            else:
                st.markdown("*No suggestion available*")

        st.markdown("---")

        # Search for a different member
        search_key = f"search_{item['id']}"
        search_term = st.text_input(
            "Search for correct member (name or entity code)",
            key=search_key,
            placeholder="Type to search…",
        )

        if search_term and len(search_term) >= 2:
            search_results = conn.execute(
                """
                SELECT id, name, entity_code, billing_city, billing_state
                FROM member
                WHERE is_active = 1
                  AND (lower(name) LIKE lower(?) OR lower(entity_code) LIKE lower(?))
                ORDER BY name
                LIMIT 20
                """,
                (f"%{search_term}%", f"%{search_term}%"),
            ).fetchall()
            if search_results:
                options = {
                    f"{r['name']} — {r['entity_code']} ({r['billing_city'] or ''}, {r['billing_state'] or ''})": r["id"]
                    for r in search_results
                }
                options["— None of these / add new facility —"] = None
                choice_label = st.selectbox(
                    "Select correct member",
                    list(options.keys()),
                    key=f"choice_{item['id']}",
                )
                chosen_member_id = options[choice_label]
            else:
                st.info("No matching members found.")
                chosen_member_id = None
        else:
            chosen_member_id = item.get("suggested_member_id")

        # -------------------------------------------------------------
        # Fallback: no existing member matches — create a new one
        # -------------------------------------------------------------
        new_member_id = None
        if chosen_member_id is None:
            with st.container(border=True):
                st.markdown("**Not in the database? Add it as a new facility**")
                with st.form(f"new_member_form_{item['id']}"):
                    nm_col1, nm_col2 = st.columns(2)
                    nm_name = nm_col1.text_input(
                        "Facility Name *", value=item["customer_name_sample"],
                        key=f"nm_name_{item['id']}",
                    )
                    nm_entity_code = nm_col2.text_input(
                        "Premier/Alliant Entity Code (optional)",
                        key=f"nm_ec_{item['id']}",
                    )
                    nm_col3, nm_col4, nm_col5 = st.columns(3)
                    nm_city = nm_col3.text_input(
                        "City", value=item.get("city") or "", key=f"nm_city_{item['id']}",
                    )
                    nm_state = nm_col4.text_input(
                        "State", value=item.get("state") or "", key=f"nm_state_{item['id']}",
                    )
                    nm_zip = nm_col5.text_input(
                        "Zip", value=item.get("zip") or "", key=f"nm_zip_{item['id']}",
                    )
                    create_and_match = st.form_submit_button("Create Facility & Match", type="secondary")

                if create_and_match and nm_name.strip():
                    try:
                        new_member_id = create_member(
                            conn=conn,
                            name=nm_name.strip(),
                            premier_entity_code=nm_entity_code.strip() or None,
                            city=nm_city.strip() or None,
                            state=nm_state.strip() or None,
                            zip_code=nm_zip.strip() or None,
                        )
                        st.session_state[f"new_member_id_{item['id']}"] = new_member_id
                        st.success(f"Created new facility '{nm_name.strip()}' (member id {new_member_id}). Click Approve below to match it.")
                    except Exception as e:
                        st.error(f"Error creating facility: {e}")

        if st.session_state.get(f"new_member_id_{item['id']}"):
            chosen_member_id = st.session_state[f"new_member_id_{item['id']}"]

        create_alias = st.checkbox(
            "Create alias (remember this match for future files)",
            key=f"alias_{item['id']}",
            value=True,
        )
        scope = st.radio(
            "Alias scope",
            ["supplier", "global", "job_only"],
            key=f"scope_{item['id']}",
            horizontal=True,
            help="supplier = this supplier only | global = all suppliers | job_only = this job only",
        )

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 3])
        approve = btn_col1.button(
            "Approve",
            key=f"approve_{item['id']}",
            type="primary",
            disabled=(chosen_member_id is None),
        )
        dismiss = btn_col2.button("Dismiss (no match)", key=f"dismiss_{item['id']}")

        if approve and chosen_member_id:
            try:
                updated = apply_review_decision(
                    conn=conn,
                    review_item_id=item["id"],
                    resolved_member_id=chosen_member_id,
                    resolution_scope=scope,
                    resolved_by="user",
                    notes="",
                    create_alias=create_alias,
                    supplier_profile_id=job_row["supplier_profile_id"],
                )
                st.success(f"Approved — updated {updated} row(s). Output re-aggregated.")
                st.rerun()
            except Exception as e:
                st.error(f"Error applying decision: {e}")

        if dismiss:
            try:
                apply_review_decision(
                    conn=conn,
                    review_item_id=item["id"],
                    resolved_member_id=None,
                    resolution_scope="job_only",
                    resolved_by="user",
                )
                st.info("Dismissed — this customer will appear in the Unmatched tab.")
                st.rerun()
            except Exception as e:
                st.error(f"Error dismissing: {e}")
