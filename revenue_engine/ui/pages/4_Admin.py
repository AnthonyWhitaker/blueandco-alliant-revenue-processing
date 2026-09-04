"""
Admin page — manage suppliers, members, aliases, and payment ledger.

Tabs:
  1. Members       — load/refresh Account List.xlsx
  2. Payment Ledger — load/refresh Ledger.xlsx
  3. Suppliers     — view/edit/add supplier profiles
  4. Column Maps   — view/edit column mappings per supplier
  5. Tier C Rules  — view/edit pivot extraction & allocation rules
  6. Aliases       — view/edit member aliases
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import (
    init_db, execute_upsert, execute_insert_ignore, execute_insert_or_replace,
    execute_select, get_last_insert_id,
)
from engine.member_loader import load_account_list, create_member
from engine.payment_resolver import load_ledger_from_excel
from engine.file_reader import infer_column_map
from engine.constants import (
    CANONICAL_CUSTOMER_NAME, CANONICAL_JDE_NUMBER, CANONICAL_PROCESS_DATE,
    CANONICAL_CITY, CANONICAL_STATE, CANONICAL_ZIP, CANONICAL_ADDRESS,
    CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT, CANONICAL_PRODUCT_LINE,
    CANONICAL_CONTRACT_NUMBER,
    JOB_TYPE_PIVOT_ALLOCATION,
    EXTRACT_NAMED_CELL, EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM,
    EXTRACT_COLUMN_SUM, EXTRACT_FORMULA, EXTRACT_MANUAL_ENTRY,
    ALLOC_PERCENT, ALLOC_FIXED_AMOUNT, ALLOC_REMAINDER,
)

st.set_page_config(page_title="Admin | Revenue Processing", layout="wide")
st.title("⚙️ Admin")

conn = st.session_state.get("conn") or init_db()

tab_members, tab_ledger, tab_suppliers, tab_colmap, tab_tierc, tab_aliases = st.tabs([
    "Members", "Payment Ledger", "Suppliers", "Column Maps", "Tier C Rules", "Aliases"
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
        rows = execute_select(
            conn,
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
        rows = execute_select(
            conn,
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
        "also configure its extraction and allocation rules in the **Tier C Rules** tab."
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
# Tier C Rules tab (pivot_allocation extraction + allocation rules)
# ------------------------------------------------------------------
with tab_tierc:
    st.subheader("Tier C Extraction & Allocation Rules")
    st.caption(
        "For pivot_allocation suppliers only — declares WHERE to find each dollar "
        "total on an aggregate/pivot workpaper and WHO it gets allocated to."
    )

    tierc_suppliers = execute_select(
        conn,
        "SELECT id, name FROM supplier_profile WHERE job_type = ? ORDER BY name",
        (JOB_TYPE_PIVOT_ALLOCATION,),
    ).fetchall()

    if not tierc_suppliers:
        st.info(
            "No suppliers are configured with job_type = 'pivot_allocation' yet. "
            "Set that in the Suppliers tab first."
        )
    else:
        tc_choice = st.selectbox(
            "Select Supplier",
            [s["name"] for s in tierc_suppliers],
            key="tierc_supplier",
        )
        tc_sp_id = next(s["id"] for s in tierc_suppliers if s["name"] == tc_choice)

        # --- Existing extraction rules ---
        st.markdown("#### Extraction Rules")
        extraction_rows = execute_select(
            conn,
            "SELECT * FROM supplier_extraction_rule WHERE supplier_profile_id = ? ORDER BY sort_order, id",
            (tc_sp_id,),
        ).fetchall()
        er_display_cols = [
            "rule_name", "sheet_name", "extraction_method", "cell_address", "label_text",
            "label_match_mode", "value_row_offset", "value_col_offset", "range_axis",
            "column_index", "column_letter", "header_row", "stop_label_text",
            "formula_expression", "is_sales_amount", "sort_order",
        ]
        if extraction_rows:
            df_er = pd.DataFrame([dict(r) for r in extraction_rows])
            df_er = df_er[[c for c in er_display_cols if c in df_er.columns]]
            st.dataframe(df_er, use_container_width=True, hide_index=True)
        else:
            st.info("No extraction rules yet for this supplier.")

        # --- Existing allocation rules ---
        st.markdown("#### Allocation Rules")
        allocation_rows = execute_select(
            conn,
            """
            SELECT a.id, a.extraction_rule_name, a.member_id, m.name AS member_name, m.entity_code,
                   a.allocation_type, a.allocation_value, a.contract_number,
                   a.product_line, a.is_active
            FROM supplier_allocation_rule a
            JOIN member m ON m.id = a.member_id
            WHERE a.supplier_profile_id = ?
            ORDER BY a.extraction_rule_name, m.name
            """,
            (tc_sp_id,),
        ).fetchall()
        if allocation_rows:
            st.dataframe(
                pd.DataFrame([dict(r) for r in allocation_rows]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("No allocation rules yet for this supplier.")

        st.divider()

        # --- Add / Edit Extraction Rule ---
        st.markdown("#### Add / Edit Extraction Rule")
        er_names = [r["rule_name"] for r in extraction_rows]
        er_mode = "Add new"
        if er_names:
            er_mode = st.radio("Extraction rule", ["Add new", "Edit existing"], horizontal=True, key="er_mode")
        er_existing = None
        if er_mode == "Edit existing" and er_names:
            er_edit_name = st.selectbox("Rule to edit", er_names, key="er_edit_name")
            er_existing = next(dict(r) for r in extraction_rows if r["rule_name"] == er_edit_name)

        d = er_existing or {}
        # NOTE: these are plain top-level widgets, not st.form — the extraction_method
        # selection must trigger an immediate rerun so method-specific fields can
        # appear/disappear. This is a deliberate deviation from this file's other
        # (static) forms.
        er_rule_name = st.text_input("Rule Name *", value=d.get("rule_name", ""), key="er_rule_name")
        er_sheet = st.text_input("Sheet name (blank = first sheet)", value=d.get("sheet_name") or "", key="er_sheet")
        er_method = st.selectbox(
            "Extraction Method",
            [EXTRACT_NAMED_CELL, EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM,
             EXTRACT_COLUMN_SUM, EXTRACT_FORMULA, EXTRACT_MANUAL_ENTRY],
            index=[EXTRACT_NAMED_CELL, EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM,
                   EXTRACT_COLUMN_SUM, EXTRACT_FORMULA, EXTRACT_MANUAL_ENTRY].index(d.get("extraction_method", EXTRACT_NAMED_CELL)),
            key="er_method",
        )

        er_cell_address = er_label_text = er_match_mode = er_formula = er_stop_label = None
        er_match_threshold = 90
        er_row_offset, er_col_offset = 1, 0
        er_range_axis = "column"
        er_col_index, er_col_letter = None, None
        er_header_row = 0

        if er_method == EXTRACT_NAMED_CELL:
            er_cell_address = st.text_input("Cell Address * (e.g. E8)", value=d.get("cell_address") or "", key="er_cell")
        elif er_method in (EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM):
            er_label_text = st.text_input("Label Text *", value=d.get("label_text") or "", key="er_label")
            er_match_mode = st.selectbox(
                "Label Match Mode", ["exact", "contains", "startswith", "fuzzy"],
                index=["exact", "contains", "startswith", "fuzzy"].index(d.get("label_match_mode") or "exact"),
                key="er_match_mode",
            )
            if er_match_mode == "fuzzy":
                er_match_threshold = st.number_input(
                    "Fuzzy Match Threshold (0-100)", min_value=0, max_value=100,
                    value=int(d.get("label_match_threshold") or 90), key="er_threshold",
                )
            if er_method == EXTRACT_LABEL_LOOKUP:
                c1, c2 = st.columns(2)
                er_row_offset = c1.number_input("Value Row Offset", value=int(d.get("value_row_offset") if d.get("value_row_offset") is not None else 1), key="er_row_off")
                er_col_offset = c2.number_input("Value Col Offset", value=int(d.get("value_col_offset") or 0), key="er_col_off")
            else:
                er_range_axis = st.selectbox(
                    "Range Axis", ["column", "row"],
                    index=["column", "row"].index(d.get("range_axis") or "column"), key="er_axis",
                )
                er_stop_label = st.text_input("Stop Label Text (optional boundary)", value=d.get("stop_label_text") or "", key="er_stop")
        elif er_method == EXTRACT_COLUMN_SUM:
            col_spec_mode = st.radio("Specify column by", ["Index", "Letter"], horizontal=True, key="er_col_spec")
            if col_spec_mode == "Index":
                er_col_index = st.number_input("Column Index (0-based) *", min_value=0, value=int(d.get("column_index") or 0), key="er_col_idx")
            else:
                er_col_letter = st.text_input("Column Letter * (e.g. F)", value=d.get("column_letter") or "", key="er_col_ltr")
            er_header_row = st.number_input("Header Row (0-indexed, sum starts below it)", min_value=0, value=int(d.get("header_row") or 0), key="er_hdr_row")
        elif er_method == EXTRACT_FORMULA:
            other_names = [n for n in er_names if n != d.get("rule_name")]
            if other_names:
                st.caption("Other rule names available to reference: " + ", ".join(other_names))
            er_formula = st.text_area("Formula Expression * (e.g. \"Gross - Fees Withheld\")", value=d.get("formula_expression") or "", key="er_formula")
        else:
            st.caption("manual_entry: no automated parsing — a human enters the confirmed total during Upload/Review.")

        er_is_sales = st.checkbox(
            "Is Sales Amount (feeds volume instead of fee_amount)",
            value=bool(d.get("is_sales_amount")), key="er_is_sales",
        )
        er_sort_order = st.number_input("Sort Order", value=int(d.get("sort_order") or 0), key="er_sort")

        if er_method == EXTRACT_FORMULA and er_formula:
            for n1 in er_names:
                for n2 in er_names:
                    if n1 != n2 and n1 in n2:
                        st.warning(f"Rule name {n1!r} is a substring of {n2!r} — formula references can collide. Consider distinct names.")

        er_save_col, er_del_col = st.columns([1, 1])
        if er_save_col.button("Save Extraction Rule", type="primary", key="er_save"):
            if not er_rule_name.strip():
                st.error("Rule Name is required.")
            elif er_method == EXTRACT_NAMED_CELL and not er_cell_address.strip():
                st.error("Cell Address is required for named_cell.")
            elif er_method in (EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM) and not er_label_text.strip():
                st.error("Label Text is required for this method.")
            elif er_method == EXTRACT_COLUMN_SUM and er_col_index is None and not (er_col_letter or "").strip():
                st.error("Column Index or Column Letter is required for column_sum.")
            elif er_method == EXTRACT_FORMULA and not er_formula.strip():
                st.error("Formula Expression is required for formula.")
            else:
                old_name = d.get("rule_name")
                params = (
                    tc_sp_id, er_rule_name.strip(), er_sheet.strip() or None, er_method,
                    er_cell_address.strip() if er_cell_address else None,
                    er_label_text.strip() if er_label_text else None,
                    er_match_mode, int(er_match_threshold),
                    int(er_row_offset), int(er_col_offset), er_range_axis,
                    int(er_col_index) if er_col_index is not None else None,
                    er_col_letter.strip() if er_col_letter else None,
                    int(er_header_row),
                    er_stop_label.strip() if er_stop_label else None,
                    er_formula.strip() if er_formula else None,
                    int(er_is_sales), int(er_sort_order),
                )
                try:
                    if er_existing:
                        cur = conn.execute(
                            """
                            UPDATE supplier_extraction_rule SET
                                supplier_profile_id=?, rule_name=?, sheet_name=?, extraction_method=?,
                                cell_address=?, label_text=?, label_match_mode=?, label_match_threshold=?,
                                value_row_offset=?, value_col_offset=?, range_axis=?, column_index=?,
                                column_letter=?, header_row=?, stop_label_text=?, formula_expression=?,
                                is_sales_amount=?, sort_order=?
                            WHERE id = ?
                            """,
                            params + (er_existing["id"],),
                        )
                        if old_name and old_name != er_rule_name.strip():
                            n_refs = conn.execute(
                                "SELECT COUNT(*) FROM supplier_allocation_rule WHERE supplier_profile_id=? AND extraction_rule_name=?",
                                (tc_sp_id, old_name),
                            ).fetchone()[0]
                            if n_refs:
                                conn.execute(
                                    "UPDATE supplier_allocation_rule SET extraction_rule_name=? WHERE supplier_profile_id=? AND extraction_rule_name=?",
                                    (er_rule_name.strip(), tc_sp_id, old_name),
                                )
                                st.warning(f"Renamed — updated {n_refs} allocation rule(s) referencing the old name. Check any 'formula' rules referencing {old_name!r} manually.")
                    else:
                        conn.execute(
                            """
                            INSERT INTO supplier_extraction_rule
                                (supplier_profile_id, rule_name, sheet_name, extraction_method,
                                 cell_address, label_text, label_match_mode, label_match_threshold,
                                 value_row_offset, value_col_offset, range_axis, column_index,
                                 column_letter, header_row, stop_label_text, formula_expression,
                                 is_sales_amount, sort_order)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            params,
                        )
                    conn.commit()
                    st.success(f"Extraction rule '{er_rule_name.strip()}' saved.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e} (rule names must be unique per supplier)")

        if er_existing and er_del_col.button("Delete Extraction Rule", key="er_delete"):
            n_alloc_refs = conn.execute(
                "SELECT COUNT(*) FROM supplier_allocation_rule WHERE supplier_profile_id=? AND extraction_rule_name=?",
                (tc_sp_id, er_existing["rule_name"]),
            ).fetchone()[0]
            if n_alloc_refs:
                st.error(f"Cannot delete: {n_alloc_refs} allocation rule(s) reference '{er_existing['rule_name']}'. Delete those first.")
            else:
                conn.execute("DELETE FROM supplier_extraction_rule WHERE id = ?", (er_existing["id"],))
                conn.commit()
                st.success(f"Deleted extraction rule '{er_existing['rule_name']}'.")
                st.rerun()

        st.divider()

        # --- Add / Edit Allocation Rule ---
        st.markdown("#### Add / Edit Allocation Rule")
        current_er_names = [r["rule_name"] for r in extraction_rows]
        if not current_er_names:
            st.info("Add an extraction rule first — allocation rules link to one by name.")
        else:
            ar_mode = "Add new"
            if allocation_rows:
                ar_mode = st.radio("Allocation rule", ["Add new", "Edit existing"], horizontal=True, key="ar_mode")
            ar_existing = None
            if ar_mode == "Edit existing" and allocation_rows:
                ar_options = {f"{r['extraction_rule_name']} → {r['member_name']}": r for r in allocation_rows}
                ar_pick = st.selectbox("Rule to edit", list(ar_options.keys()), key="ar_edit_pick")
                ar_existing = dict(ar_options[ar_pick])

            ad = ar_existing or {}
            ar_ext_name = st.selectbox(
                "Extraction Rule *", current_er_names,
                index=current_er_names.index(ad["extraction_rule_name"]) if ad.get("extraction_rule_name") in current_er_names else 0,
                key="ar_ext_name",
            )

            ar_member_id = ad.get("member_id")
            member_search = st.text_input("Search member by name or entity code *", key="ar_member_search")
            if member_search and len(member_search) >= 2:
                members_found = execute_select(
                    conn,
                    "SELECT id, name, entity_code FROM member WHERE lower(name) LIKE lower(?) OR lower(entity_code) LIKE lower(?) ORDER BY name LIMIT 20",
                    (f"%{member_search}%", f"%{member_search}%"),
                ).fetchall()
                if members_found:
                    member_opts = {f"{r['name']} ({r['entity_code']})": r["id"] for r in members_found}
                    chosen_ar_member = st.selectbox("Select Member", list(member_opts.keys()), key="ar_member_choice")
                    ar_member_id = member_opts[chosen_ar_member]
            elif ar_existing:
                st.caption(f"Currently: {ar_existing['member_name']} ({ar_existing.get('entity_code')})")

            ar_type = st.selectbox(
                "Allocation Type", [ALLOC_PERCENT, ALLOC_FIXED_AMOUNT, ALLOC_REMAINDER],
                index=[ALLOC_PERCENT, ALLOC_FIXED_AMOUNT, ALLOC_REMAINDER].index(ad.get("allocation_type", ALLOC_PERCENT)),
                key="ar_type",
            )
            ar_value = None
            if ar_type != ALLOC_REMAINDER:
                ar_value = st.number_input(
                    "Allocation Value (% for percent, $ for fixed_amount) *",
                    value=float(ad.get("allocation_value") or 0.0), key="ar_value",
                )
            else:
                st.caption("Remainder: gets whatever is left after other rows for this extraction rule are applied.")
            c1, c2 = st.columns(2)
            ar_contract = c1.text_input("Contract Number (optional override)", value=ad.get("contract_number") or "", key="ar_contract")
            ar_product = c2.text_input("Product Line (optional)", value=ad.get("product_line") or "", key="ar_product")
            ar_active = st.checkbox("Active", value=bool(ad.get("is_active", True)), key="ar_active")

            # --- Validation warnings ---
            other_rows_for_ext = [
                r for r in allocation_rows
                if r["extraction_rule_name"] == ar_ext_name and (not ar_existing or r["id"] != ar_existing["id"])
            ]
            n_remainder = sum(1 for r in other_rows_for_ext if r["allocation_type"] == ALLOC_REMAINDER)
            if ar_type == ALLOC_REMAINDER and n_remainder >= 1:
                st.warning(f"Extraction rule '{ar_ext_name}' already has a remainder row — only the first is used at runtime.")
            pct_sum = sum(
                float(r["allocation_value"] or 0) for r in other_rows_for_ext
                if r["allocation_type"] == ALLOC_PERCENT and r["is_active"]
            )
            if ar_type == ALLOC_PERCENT and ar_value:
                pct_sum += ar_value
            if pct_sum > 100:
                st.warning(f"Active percent allocations for '{ar_ext_name}' now sum to {pct_sum:.1f}% (>100%).")
            elif pct_sum > 0 and pct_sum < 100 and not other_rows_for_ext and ar_type != ALLOC_REMAINDER:
                st.info(f"Percent allocations for '{ar_ext_name}' sum to {pct_sum:.1f}% — add a remainder row or more rules to cover the full total.")

            ar_save_col, ar_del_col = st.columns([1, 1])
            if ar_save_col.button("Save Allocation Rule", type="primary", key="ar_save"):
                if not ar_member_id:
                    st.error("Select a member first.")
                elif ar_type != ALLOC_REMAINDER and not ar_value:
                    st.error("Allocation Value is required for percent/fixed_amount.")
                else:
                    a_params = (
                        tc_sp_id, ar_ext_name, int(ar_member_id), ar_type,
                        float(ar_value) if ar_type != ALLOC_REMAINDER else None,
                        ar_contract.strip() or None, ar_product.strip() or None, int(ar_active),
                    )
                    try:
                        if ar_existing:
                            conn.execute(
                                """
                                UPDATE supplier_allocation_rule SET
                                    supplier_profile_id=?, extraction_rule_name=?, member_id=?,
                                    allocation_type=?, allocation_value=?, contract_number=?,
                                    product_line=?, is_active=?
                                WHERE id = ?
                                """,
                                a_params + (ar_existing["id"],),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO supplier_allocation_rule
                                    (supplier_profile_id, extraction_rule_name, member_id,
                                     allocation_type, allocation_value, contract_number,
                                     product_line, is_active)
                                VALUES (?,?,?,?,?,?,?,?)
                                """,
                                a_params,
                            )
                        conn.commit()
                        st.success("Allocation rule saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e} (one allocation row per member per extraction rule)")

            if ar_existing and ar_del_col.button("Delete Allocation Rule", key="ar_delete"):
                conn.execute("DELETE FROM supplier_allocation_rule WHERE id = ?", (ar_existing["id"],))
                conn.commit()
                st.success("Deleted allocation rule.")
                st.rerun()

with tab_aliases:
    st.subheader("Member Aliases")
    st.caption("Alternate names for members — used in Tier 2 matching (before fuzzy).")

    aliases = execute_select(
        conn,
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
        members_found = execute_select(
            conn,
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
