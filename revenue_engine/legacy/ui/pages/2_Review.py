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

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # revenue_engine/

from engine.db import init_db
from core.jobs import list_jobs
from core.members import create_member, search_members
from core.reviews import approve, dismiss, list_pending_items
from models.members import MemberInput
from models.reviews import ReviewApproval

st.set_page_config(page_title="Review | Revenue Processing", layout="wide")
st.title("🔍 Review — Unmatched Customers")

conn = st.session_state.get("conn") or init_db()

# Filter by job
jobs_with_pending = list_jobs(conn, has_pending_reviews=True)

if not jobs_with_pending:
    st.success("No pending reviews! All customers are matched.")
    st.stop()

job_options = {
    f"Job #{j.id} — {j.supplier_name} — {j.original_file_name} ({j.pending_review_count} pending)": j.id
    for j in jobs_with_pending
}
selected_label = st.selectbox("Select Job to Review", list(job_options.keys()))
selected_job_id = job_options[selected_label]

st.divider()

# Load pending review items for this job
review_items = list_pending_items(conn, selected_job_id)

st.markdown(f"### {len(review_items)} unmatched customer(s) — sorted by revenue impact")
st.caption("Resolve each one below. Approved matches update the output file automatically.")

for item in review_items:
    with st.expander(
        f"**{item.customer_name_sample}**  —  "
        f"JDE: `{item.jde_number or '—'}`  |  "
        f"Fee: `${(item.total_fee or 0):.2f}`  |  "
        f"{item.affected_row_count} row(s)",
        expanded=True,
    ):
        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("**Unmatched Customer**")
            st.markdown(f"- **Name:** {item.customer_name_sample}")
            st.markdown(f"- **JDE #:** {item.jde_number or '—'}")
            st.markdown(f"- **Location:** {item.city or ''}, {item.state or ''} {item.zip or ''}")
            st.markdown(f"- **Fee Amount:** ${(item.total_fee or 0):,.2f}")
            st.markdown(f"- **Rows Affected:** {item.affected_row_count}")

        with col_right:
            if item.suggestion:
                s = item.suggestion
                st.markdown("**Best Suggestion**")
                st.markdown(f"- **Name:** {s.name}")
                st.markdown(f"- **Entity Code:** `{s.entity_code or '—'}`")
                st.markdown(f"- **Location:** {s.city or ''}, {s.state or ''}")
                color = "green" if s.score >= 80 else ("orange" if s.score >= 60 else "red")
                st.markdown(f"- **Match Score:** :{color}[{s.score}]")
            else:
                st.markdown("*No suggestion available*")

        st.markdown("---")

        # Search for a different member
        search_term = st.text_input(
            "Search for correct member (name or entity code)",
            key=f"search_{item.id}",
            placeholder="Type to search…",
        )

        if search_term and len(search_term) >= 2:
            search_results = search_members(conn, term=search_term, active_only=True, limit=20).items
            if search_results:
                options = {
                    f"{m.name} — {m.entity_code} ({m.billing_city or ''}, {m.billing_state or ''})": m.id
                    for m in search_results
                }
                options["— None of these / add new facility —"] = None
                choice_label = st.selectbox(
                    "Select correct member",
                    list(options.keys()),
                    key=f"choice_{item.id}",
                )
                chosen_member_id = options[choice_label]
            else:
                st.info("No matching members found.")
                chosen_member_id = None
        else:
            chosen_member_id = item.suggestion.member_id if item.suggestion else None

        # -------------------------------------------------------------
        # Fallback: no existing member matches — create a new one
        # -------------------------------------------------------------
        if chosen_member_id is None:
            with st.container(border=True):
                st.markdown("**Not in the database? Add it as a new facility**")
                with st.form(f"new_member_form_{item.id}"):
                    nm_col1, nm_col2 = st.columns(2)
                    nm_name = nm_col1.text_input(
                        "Facility Name *", value=item.customer_name_sample or "",
                        key=f"nm_name_{item.id}",
                    )
                    nm_entity_code = nm_col2.text_input(
                        "Premier/Alliant Entity Code (optional)",
                        key=f"nm_ec_{item.id}",
                    )
                    if not nm_entity_code.strip():
                        st.caption(
                            "⚠️ Without an entity code, this facility's revenue "
                            "will still count toward the job total, but the "
                            "output row will use a placeholder PEC until a "
                            "real one is added in Admin > Members."
                        )
                    nm_col3, nm_col4, nm_col5 = st.columns(3)
                    nm_city = nm_col3.text_input("City", value=item.city or "", key=f"nm_city_{item.id}")
                    nm_state = nm_col4.text_input("State", value=item.state or "", key=f"nm_state_{item.id}")
                    nm_zip = nm_col5.text_input("Zip", value=item.zip or "", key=f"nm_zip_{item.id}")
                    create_and_match = st.form_submit_button("Create Facility & Match", type="secondary")

                if create_and_match and nm_name.strip():
                    try:
                        new_member = create_member(conn, MemberInput(
                            name=nm_name, premier_entity_code=nm_entity_code or None,
                            city=nm_city or None, state=nm_state or None, zip=nm_zip or None,
                        ))
                        st.session_state[f"new_member_id_{item.id}"] = new_member.id
                        st.success(f"Created new facility '{new_member.name}' (member id {new_member.id}). Click Approve below to match it.")
                    except Exception as e:
                        st.error(f"Error creating facility: {e}")

        if st.session_state.get(f"new_member_id_{item.id}"):
            chosen_member_id = st.session_state[f"new_member_id_{item.id}"]

        create_alias = st.checkbox(
            "Create alias (remember this match for future files)",
            key=f"alias_{item.id}",
            value=True,
        )
        scope = st.radio(
            "Alias scope",
            ["supplier", "global", "job_only"],
            key=f"scope_{item.id}",
            horizontal=True,
            help="supplier = this supplier only | global = all suppliers | job_only = this job only (no alias is saved)",
        )

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 3])
        approve_clicked = btn_col1.button(
            "Approve",
            key=f"approve_{item.id}",
            type="primary",
            disabled=(chosen_member_id is None),
        )
        dismiss_clicked = btn_col2.button("Dismiss (no match)", key=f"dismiss_{item.id}")

        if approve_clicked and chosen_member_id:
            try:
                result = approve(conn, item.id, ReviewApproval(
                    member_id=chosen_member_id, create_alias=create_alias, alias_scope=scope,
                ))
                st.success(f"Approved — updated {result.updated_rows} row(s). Output re-aggregated.")
                st.rerun()
            except Exception as e:
                st.error(f"Error applying decision: {e}")

        if dismiss_clicked:
            try:
                dismiss(conn, item.id)
                st.info("Dismissed — this customer will appear in the Unmatched tab.")
                st.rerun()
            except Exception as e:
                st.error(f"Error dismissing: {e}")
