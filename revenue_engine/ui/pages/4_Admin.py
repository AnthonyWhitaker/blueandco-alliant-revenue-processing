"""
Admin page — manage suppliers, members, aliases, and payment ledger.

Tabs:
  1. Members       — load/refresh Account List.xlsx
  2. Payment Ledger — load/refresh Ledger.xlsx
  3. Suppliers     — view/edit/add supplier profiles
  4. Column Maps   — view/edit column mappings per supplier
  5. Aliases       — view/edit member aliases
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import init_db, execute_upsert, execute_insert_ignore, execute_insert_or_replace
from engine.member_loader import load_account_list, create_member
from engine.payment_resolver import load_ledger_from_excel
from engine.file_reader import infer_column_map
from engine.constants import (
    CANONICAL_CUSTOMER_NAME, CANONICAL_JDE_NUMBER, CANONICAL_PROCESS_DATE,
    CANONICAL_CITY, CANONICAL_STATE, CANONICAL_ZIP, CANONICAL_ADDRESS,
    CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT, CANONICAL_PRODUCT_LINE,
    CANONICAL_CONTRACT_NUMBER,
)

st.set_page_config(page_title="Admin | Revenue Processing", layout="wide")
st.title("⚙️ Admin")

conn = st.session_state.get("conn") or init_db()

tab_members, tab_ledger, tab_suppliers, tab_colmap, tab_aliases = st.tabs([
    "Members", "Payment Ledger", "Suppliers", "Column Maps", "Aliases"
])

# ------------------------------------------------------------------
# Members tab
# ------------------------------------------------------------------
with tab_members:
    st.subheader("Member / Account List")

    n_members = conn.execute("SELECT COUNT(*) FROM member").fetchone()[0]
    st.metric("Members in Database", f"{n_members:,}")

    st.markdown("Upload the **Account List.xlsx** to load or refresh the member database.")
    acct_file = st.file_uploader("Account List.xlsx", type=["xlsx", "xls"], key="acct_upload")
    replace_all = st.checkbox("Replace all existing members (full refresh)", value=False)

    if acct_file and st.button("Load Account List", type="primary"):
        with st.spinner("Loading…"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                tmp.write(acct_file.read())
                tmp_path = tmp.name
            try:
                inserted, skipped = load_account_list(conn, tmp_path, replace_all=replace_all)
                st.success(f"Loaded: {inserted:,} members inserted/updated, {skipped} skipped.")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()
    if st.button("Show sample members"):
        rows = conn.execute(
            "SELECT entity_code, name, billing_city, billing_state FROM member LIMIT 50"
        ).fetchall()
        st.dataframe(pd.DataFrame([dict(r) for r in rows]), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### Add a Single Member")
    st.caption(
        "For a real facility that's missing from the Account List (e.g. a "
        "zero-match customer from a supplier file) — a full Account List "
        "refresh will still override this row's city/state/zip if it later "
        "appears there."
    )
    with st.form("add_member_form"):
        am_col1, am_col2 = st.columns(2)
        am_name = am_col1.text_input("Facility Name *")
        am_entity_code = am_col2.text_input("Premier/Alliant Entity Code (optional)")
        am_col3, am_col4 = st.columns(2)
        am_account_id = am_col3.text_input("Salesforce Account ID (optional)")
        am_col5, am_col6, am_col7 = st.columns(3)
        am_city = am_col5.text_input("City")
        am_state = am_col6.text_input("State")
        am_zip = am_col7.text_input("Zip")
        am_submitted = st.form_submit_button("Add Member", type="primary")

    if am_submitted and am_name.strip():
        try:
            new_id = create_member(
                conn=conn,
                name=am_name.strip(),
                account_id=am_account_id.strip() or None,
                premier_entity_code=am_entity_code.strip() or None,
                city=am_city.strip() or None,
                state=am_state.strip() or None,
                zip_code=am_zip.strip() or None,
            )
            st.success(f"Added '{am_name.strip()}' as member id {new_id}.")
        except Exception as e:
            st.error(f"Error: {e}")

# ------------------------------------------------------------------
# Payment Ledger tab
# ------------------------------------------------------------------
with tab_ledger:
    st.subheader("Payment Ledger")

    n_ledger = conn.execute("SELECT COUNT(*) FROM payment_ledger").fetchone()[0]
    st.metric("Ledger Entries", f"{n_ledger:,}")

    st.markdown("Upload the **Ledger.xlsx** to import Payment IDs for deposit amounts.")
    ledger_file = st.file_uploader("Ledger.xlsx", type=["xlsx", "xls"], key="ledger_upload")
    ledger_sheet = st.text_input("Sheet name", value="Ledger")

    if ledger_file and st.button("Load Ledger", type="primary"):
        with st.spinner("Loading…"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                tmp.write(ledger_file.read())
                tmp_path = tmp.name
            try:
                count = load_ledger_from_excel(conn, tmp_path, sheet_name=ledger_sheet)
                st.success(f"Loaded {count} ledger entries.")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()
    if st.button("Show recent ledger entries"):
        rows = conn.execute(
            "SELECT payor, payment_received_date, deposit_amount, sf_payment_id FROM payment_ledger ORDER BY payment_received_date DESC LIMIT 30"
        ).fetchall()
        st.dataframe(pd.DataFrame([dict(r) for r in rows]), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------
# Suppliers tab
# ------------------------------------------------------------------
with tab_suppliers:
    st.subheader("Supplier Profiles")

    suppliers = conn.execute("SELECT * FROM supplier_profile ORDER BY name").fetchall()

    if suppliers:
        df_sp = pd.DataFrame([dict(s) for s in suppliers])
        display_cols = ["id", "name", "contract_id", "revenue_type", "keep_return_default",
                        "payor_filter_name", "payment_id_source", "job_type",
                        "header_row", "max_read_columns", "requires_manual_confirmation"]
        display_cols = [c for c in display_cols if c in df_sp.columns]
        st.dataframe(df_sp[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No suppliers configured. Add one below or run `scripts/seed_guerbet.py`.")

    st.divider()
    st.markdown("### Add / Update Supplier Profile")
    st.caption(
        "For a pivot/aggregate-workpaper supplier (job_type = pivot_allocation), "
        "also configure its extraction and allocation rules via "
        "`scripts/seed_tier_ab_suppliers.py`-style seed script — there is no "
        "rule editor in this UI yet."
    )

    with st.form("supplier_form"):
        s_name      = st.text_input("Supplier Name *", placeholder="Guerbet")
        s_desc      = st.text_input("Description")
        s_contract  = st.text_input("Salesforce Contract ID")
        s_rev_type  = st.selectbox("Revenue Type", ["Admin Fee", "Rebate", "Other"], index=0)
        s_keep      = st.selectbox("Default Keep/Return", ["Keep", "Return"], index=0)
        s_payor     = st.text_input("Payor Filter Name (for ledger lookup)", placeholder="Guerbet")
        s_pid_src   = st.selectbox("Payment ID Source", ["ledger", "manual", "column"], index=0)
        s_skip_col  = st.text_input("Strip summary rows where column is blank", placeholder="JDE Customer Number")
        s_sheet     = st.text_input("Sheet name (blank = first sheet)")
        s_header_row = st.number_input("Header row (0-indexed)", min_value=0, value=0, step=1)
        s_max_cols  = st.number_input("Max columns to read (0 = no limit)", min_value=0, value=0, step=1)
        s_job_type  = st.selectbox("Job Type", ["row_level", "pivot_allocation"], index=0,
                                    help="row_level = facility-level rows matched by MemberMatcher (Tier A/B). "
                                         "pivot_allocation = aggregate/pivot workpaper with no facility rows (Tier C).")
        s_needs_confirm = st.checkbox("Requires manual confirmation before completing (recommended for new Tier C suppliers)")
        submitted   = st.form_submit_button("Save Supplier", type="primary")

    if submitted and s_name:
        try:
            execute_upsert(
                conn,
                """
                INSERT INTO supplier_profile
                    (name, description, contract_id, revenue_type, keep_return_default,
                     payor_filter_name, payment_id_source, skip_rows_where_col_null, sheet_name,
                     header_row, max_read_columns, job_type, requires_manual_confirmation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description             = excluded.description,
                    contract_id             = excluded.contract_id,
                    revenue_type            = excluded.revenue_type,
                    keep_return_default     = excluded.keep_return_default,
                    payor_filter_name       = excluded.payor_filter_name,
                    payment_id_source       = excluded.payment_id_source,
                    skip_rows_where_col_null = excluded.skip_rows_where_col_null,
                    sheet_name              = excluded.sheet_name,
                    header_row              = excluded.header_row,
                    max_read_columns        = excluded.max_read_columns,
                    job_type                = excluded.job_type,
                    requires_manual_confirmation = excluded.requires_manual_confirmation,
                    updated_at              = CURRENT_TIMESTAMP
                """,
                (s_name, s_desc, s_contract or None, s_rev_type, s_keep,
                 s_payor or None, s_pid_src, s_skip_col or None, s_sheet or None,
                 int(s_header_row), int(s_max_cols) or None, s_job_type, int(s_needs_confirm)),
            )
            conn.commit()
            st.success(f"Supplier '{s_name}' saved.")
        except Exception as e:
            st.error(f"Error: {e}")

# ------------------------------------------------------------------
# Column Maps tab
# ------------------------------------------------------------------
with tab_colmap:
    st.subheader("Supplier Column Mappings")
    st.caption("Maps each supplier's raw file column names to canonical engine fields.")

    suppliers_list = conn.execute("SELECT id, name FROM supplier_profile ORDER BY name").fetchall()
    if not suppliers_list:
        st.info("Add a supplier first.")
    else:
        sp_choice = st.selectbox(
            "Select Supplier",
            [s["name"] for s in suppliers_list],
            key="colmap_supplier",
        )
        sp_id = next(s["id"] for s in suppliers_list if s["name"] == sp_choice)

        existing = conn.execute(
            "SELECT * FROM supplier_column_map WHERE supplier_profile_id = ? ORDER BY source_column",
            (sp_id,),
        ).fetchall()

        if existing:
            st.dataframe(
                pd.DataFrame([dict(r) for r in existing])[["source_column", "canonical_field", "is_required"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No column mappings yet. Add manually below or use the auto-infer tool.")

        st.divider()

        # Auto-infer from sample file
        st.markdown("#### Auto-Infer from Sample File")
        sample_file = st.file_uploader("Upload a sample supplier file to auto-detect columns", type=["xlsx", "xls", "csv"], key="infer_file")
        if sample_file and st.button("Infer Column Map"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(sample_file.name).suffix) as tmp:
                tmp.write(sample_file.read())
                tmp_path = tmp.name
            df_sample = pd.read_excel(tmp_path, nrows=5) if tmp_path.endswith(("xlsx", "xls")) else pd.read_csv(tmp_path, nrows=5)
            suggestions = infer_column_map(df_sample, sp_id, conn)
            st.markdown("**Suggested mappings** (review and save):")
            st.json(suggestions)
            st.session_state[f"infer_suggestions_{sp_id}"] = suggestions

        if f"infer_suggestions_{sp_id}" in st.session_state:
            if st.button("Save Inferred Mappings"):
                for src_col, canon in st.session_state[f"infer_suggestions_{sp_id}"].items():
                    execute_insert_or_replace(
                        conn,
                        """
                        INSERT OR REPLACE INTO supplier_column_map
                            (supplier_profile_id, source_column, canonical_field, is_required)
                        VALUES (?, ?, ?, 0)
                        """,
                        (sp_id, src_col, canon),
                    )
                conn.commit()
                st.success("Mappings saved.")

        st.divider()
        st.markdown("#### Add / Update Single Mapping")
        CANONICAL_OPTIONS = [
            CANONICAL_CUSTOMER_NAME, CANONICAL_JDE_NUMBER, CANONICAL_PROCESS_DATE,
            CANONICAL_CITY, CANONICAL_STATE, CANONICAL_ZIP, CANONICAL_ADDRESS,
            CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT, CANONICAL_PRODUCT_LINE,
            CANONICAL_CONTRACT_NUMBER,
        ]
        with st.form("colmap_form"):
            src_col  = st.text_input("Source Column (exact name in supplier file) *")
            canon    = st.selectbox("Canonical Field *", CANONICAL_OPTIONS)
            required = st.checkbox("Required?")
            saved    = st.form_submit_button("Save Mapping", type="primary")

        if saved and src_col:
            execute_insert_or_replace(
                conn,
                """
                INSERT OR REPLACE INTO supplier_column_map
                    (supplier_profile_id, source_column, canonical_field, is_required)
                VALUES (?, ?, ?, ?)
                """,
                (sp_id, src_col, canon, 1 if required else 0),
            )
            conn.commit()
            st.success(f"Saved: '{src_col}' → '{canon}'")

# ------------------------------------------------------------------
# Aliases tab
# ------------------------------------------------------------------
with tab_aliases:
    st.subheader("Member Aliases")
    st.caption("Alternate names for members — used in Tier 2 matching (before fuzzy).")

    aliases = conn.execute(
        """
        SELECT a.id, m.name AS member_name, m.entity_code, a.raw_name,
               sp.name AS supplier, a.approved_by, a.approved_at
        FROM member_alias a
        JOIN member m ON m.id = a.member_id
        LEFT JOIN supplier_profile sp ON sp.id = a.supplier_profile_id
        ORDER BY m.name, a.raw_name
        LIMIT 200
        """
    ).fetchall()

    if aliases:
        st.dataframe(
            pd.DataFrame([dict(r) for r in aliases]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No aliases yet. They are created automatically during the Review process.")

    st.divider()
    st.markdown("#### Add Manual Alias")
    member_search = st.text_input("Search member by name or entity code", key="alias_member_search")
    if member_search and len(member_search) >= 2:
        members_found = conn.execute(
            "SELECT id, name, entity_code FROM member WHERE lower(name) LIKE lower(?) OR lower(entity_code) LIKE lower(?) ORDER BY name LIMIT 20",
            (f"%{member_search}%", f"%{member_search}%"),
        ).fetchall()
        if members_found:
            member_opts = {f"{r['name']} ({r['entity_code']})": r["id"] for r in members_found}
            chosen_member = st.selectbox("Select Member", list(member_opts.keys()), key="alias_member_choice")
            chosen_member_id = member_opts[chosen_member]

            with st.form("alias_form"):
                alias_raw = st.text_input("Alias (alternate name) *")
                alias_jde = st.text_input("JDE Number (optional — ties alias to specific JDE)")
                alias_scope = st.selectbox("Scope", ["Global (all suppliers)", "Supplier-specific"])
                alias_supplier = None
                if alias_scope == "Supplier-specific":
                    sup_names = [s["name"] for s in conn.execute("SELECT name FROM supplier_profile ORDER BY name").fetchall()]
                    alias_sup_name = st.selectbox("Supplier", sup_names)
                    alias_supplier = conn.execute("SELECT id FROM supplier_profile WHERE name = ?", (alias_sup_name,)).fetchone()["id"]
                alias_saved = st.form_submit_button("Save Alias", type="primary")

            if alias_saved and alias_raw:
                from engine.normalizer import normalize_key
                try:
                    execute_insert_ignore(
                        conn,
                        """
                        INSERT OR IGNORE INTO member_alias
                            (member_id, supplier_profile_id, raw_name, canonical_key, jde_number, approved_by, approved_at)
                        VALUES (?, ?, ?, ?, ?, 'admin', CURRENT_TIMESTAMP)
                        """,
                        (chosen_member_id, alias_supplier, alias_raw, normalize_key(alias_raw), alias_jde or None),
                    )
                    conn.commit()
                    st.success(f"Alias '{alias_raw}' saved.")
                except Exception as e:
                    st.error(f"Error: {e}")
