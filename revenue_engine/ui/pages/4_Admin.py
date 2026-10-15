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
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from engine.db import init_db
from core import column_maps, members, misc, suppliers, tier_c
from core.errors import CoreError
from models.column_maps import ColumnMappingInput, InferredMappingInput
from models.members import MemberInput
from models.misc import AliasInput
from models.suppliers import SupplierFormInput
from models.tier_c import AllocationRuleDraft, AllocationRuleInput, ExtractionRuleInput

st.set_page_config(page_title="Admin | Revenue Processing", layout="wide")
st.title("⚙️ Admin")

conn = st.session_state.get("conn") or init_db()
META = misc.get_meta()


def save_upload_to_temp(uploaded, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getvalue())
        return tmp.name


def first_error(e: ValidationError) -> str:
    return e.errors()[0]["msg"].removeprefix("Value error, ")


def show_notices(notices) -> None:
    for n in notices:
        (st.warning if n.level == "warning" else st.info)(n.message)


def models_frame(items, columns=None) -> pd.DataFrame:
    df = pd.DataFrame([i.model_dump() for i in items])
    return df[[c for c in columns if c in df.columns]] if columns else df


tab_members, tab_ledger, tab_suppliers, tab_colmap, tab_tierc, tab_aliases = st.tabs([
    "Members", "Payment Ledger", "Suppliers", "Column Maps", "Tier C Rules", "Aliases"
])

# ------------------------------------------------------------------
# Members tab
# ------------------------------------------------------------------
with tab_members:
    st.subheader("Member / Account List")

    st.metric("Members in Database", f"{members.search_members(conn, active_only=False, limit=1).total:,}")

    st.markdown("Upload the **Account List.xlsx** to load or refresh the member database.")
    acct_file = st.file_uploader("Account List.xlsx", type=["xlsx", "xls"], key="acct_upload")
    replace_all = st.checkbox("Replace all existing members (full refresh)", value=False)

    if acct_file and st.button("Load Account List", type="primary"):
        with st.spinner("Loading…"):
            try:
                result = members.import_account_list(conn, save_upload_to_temp(acct_file, ".xlsx"), replace_all)
                st.success(f"Loaded: {result.inserted:,} members inserted/updated, {result.skipped} skipped.")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()
    if st.button("Show sample members"):
        sample = members.search_members(conn, active_only=False, limit=50).items
        st.dataframe(models_frame(sample, ["entity_code", "name", "billing_city", "billing_state"]),
                     use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("### Add a Single Member")
    st.caption(
        "For a real facility that's missing from the Account List (e.g. a "
        "zero-match customer from a supplier file) — a full Account List "
        "refresh will still override this row's city/state/zip if it later "
        "appears there. Without an entity code, matched revenue for this "
        "facility still counts toward job totals, but its output row will "
        "use a placeholder PEC until a real one is added here."
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
            new = members.create_member(conn, MemberInput(
                name=am_name, premier_entity_code=am_entity_code or None, account_id=am_account_id or None,
                city=am_city or None, state=am_state or None, zip=am_zip or None,
            ))
            st.success(f"Added '{new.name}' as member id {new.id}.")
        except Exception as e:
            st.error(f"Error: {e}")

# ------------------------------------------------------------------
# Payment Ledger tab
# ------------------------------------------------------------------
with tab_ledger:
    st.subheader("Payment Ledger")

    st.metric("Ledger Entries", f"{members.list_ledger_entries(conn, limit=1).total:,}")

    st.markdown("Upload the **Ledger.xlsx** to import Payment IDs for deposit amounts.")
    ledger_file = st.file_uploader("Ledger.xlsx", type=["xlsx", "xls"], key="ledger_upload")
    ledger_sheet = st.text_input("Sheet name", value="Ledger")

    if ledger_file and st.button("Load Ledger", type="primary"):
        with st.spinner("Loading…"):
            try:
                result = members.import_ledger(conn, save_upload_to_temp(ledger_file, ".xlsx"), ledger_sheet)
                st.success(f"Loaded {result.inserted} ledger entries.")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()
    if st.button("Show recent ledger entries"):
        recent = members.list_ledger_entries(conn, limit=30).items
        st.dataframe(models_frame(recent, ["payor", "payment_received_date", "deposit_amount", "sf_payment_id"]),
                     use_container_width=True, hide_index=True)

# ------------------------------------------------------------------
# Suppliers tab
# ------------------------------------------------------------------
with tab_suppliers:
    st.subheader("Supplier Profiles")

    all_suppliers = suppliers.list_suppliers(conn)

    if all_suppliers:
        display_cols = ["id", "name", "contract_id", "revenue_type", "keep_return_default",
                        "payor_filter_name", "payment_id_source", "job_type",
                        "header_row", "max_read_columns", "requires_manual_confirmation"]
        st.dataframe(models_frame(all_suppliers, display_cols), use_container_width=True, hide_index=True)
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
        s_rev_type  = st.selectbox("Revenue Type", META.revenue_types, index=0)
        s_keep      = st.selectbox("Default Keep/Return", META.keep_return_options, index=0)
        s_payor     = st.text_input("Payor Filter Name (for ledger lookup)", placeholder="Guerbet")
        s_pid_src   = st.selectbox("Payment ID Source", META.payment_id_sources, index=0)
        s_skip_col  = st.text_input("Strip summary rows where column is blank", placeholder="JDE Customer Number")
        s_sheet     = st.text_input("Sheet name (blank = first sheet)")
        s_header_row = st.number_input("Header row (0-indexed)", min_value=0, value=0, step=1)
        s_max_cols  = st.number_input("Max columns to read (0 = no limit)", min_value=0, value=0, step=1)
        s_job_type  = st.selectbox("Job Type", META.job_types, index=0,
                                    help="row_level = facility-level rows matched by MemberMatcher (Tier A/B). "
                                         "pivot_allocation = aggregate/pivot workpaper with no facility rows (Tier C).")
        s_needs_confirm = st.checkbox("Requires manual confirmation before completing (recommended for new Tier C suppliers)")
        submitted   = st.form_submit_button("Save Supplier", type="primary")

    if submitted and s_name:
        try:
            suppliers.upsert_supplier_by_name(conn, SupplierFormInput(
                name=s_name, description=s_desc or None, contract_id=s_contract or None,
                revenue_type=s_rev_type, keep_return_default=s_keep, payor_filter_name=s_payor or None,
                payment_id_source=s_pid_src, skip_rows_where_col_null=s_skip_col or None,
                sheet_name=s_sheet or None, header_row=int(s_header_row),
                max_read_columns=int(s_max_cols) or None, job_type=s_job_type,
                requires_manual_confirmation=s_needs_confirm,
            ))
            st.success(f"Supplier '{s_name}' saved.")
        except Exception as e:
            st.error(f"Error: {e}")

# ------------------------------------------------------------------
# Column Maps tab
# ------------------------------------------------------------------
with tab_colmap:
    st.subheader("Supplier Column Mappings")
    st.caption("Maps each supplier's raw file column names to canonical engine fields.")

    suppliers_list = suppliers.list_suppliers(conn)
    if not suppliers_list:
        st.info("Add a supplier first.")
    else:
        sp_choice = st.selectbox(
            "Select Supplier",
            [s.name for s in suppliers_list],
            key="colmap_supplier",
        )
        sp_id = next(s.id for s in suppliers_list if s.name == sp_choice)

        existing = column_maps.list_mappings(conn, sp_id)
        if existing:
            st.dataframe(
                models_frame(existing, ["source_column", "canonical_field", "is_required"]),
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
            try:
                suggestions = column_maps.infer_from_sample(
                    conn, sp_id, save_upload_to_temp(sample_file, Path(sample_file.name).suffix))
                st.markdown("**Suggested mappings** (review and save):")
                st.json({s.source_column: s.canonical_field for s in suggestions})
                st.session_state[f"infer_suggestions_{sp_id}"] = suggestions
            except CoreError as e:
                st.error(e.message)

        if f"infer_suggestions_{sp_id}" in st.session_state:
            if st.button("Save Inferred Mappings"):
                column_maps.save_inferred_mappings(conn, sp_id, [
                    InferredMappingInput(source_column=s.source_column, canonical_field=s.canonical_field)
                    for s in st.session_state[f"infer_suggestions_{sp_id}"]
                ])
                st.success("Mappings saved.")

        st.divider()
        st.markdown("#### Add / Update Single Mapping")
        with st.form("colmap_form"):
            src_col  = st.text_input("Source Column (exact name in supplier file, or contains:<text>) *")
            canon    = st.selectbox("Canonical Field *", META.canonical_fields)
            required = st.checkbox("Required?")
            saved    = st.form_submit_button("Save Mapping", type="primary")

        if saved and src_col:
            column_maps.save_mapping(conn, sp_id, ColumnMappingInput(
                source_column=src_col, canonical_field=canon, is_required=required))
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

    tierc_suppliers = suppliers.list_suppliers(conn, job_type="pivot_allocation")

    if not tierc_suppliers:
        st.info(
            "No suppliers are configured with job_type = 'pivot_allocation' yet. "
            "Set that in the Suppliers tab first."
        )
    else:
        tc_choice = st.selectbox(
            "Select Supplier",
            [s.name for s in tierc_suppliers],
            key="tierc_supplier",
        )
        tc_sp_id = next(s.id for s in tierc_suppliers if s.name == tc_choice)

        # --- Existing extraction rules ---
        st.markdown("#### Extraction Rules")
        extraction_rows = tier_c.list_extraction_rules(conn, tc_sp_id)
        er_display_cols = [
            "rule_name", "sheet_name", "extraction_method", "cell_address", "label_text",
            "label_match_mode", "value_row_offset", "value_col_offset", "range_axis",
            "column_index", "column_letter", "header_row", "stop_label_text",
            "formula_expression", "is_sales_amount", "sort_order",
        ]
        if extraction_rows:
            st.dataframe(models_frame(extraction_rows, er_display_cols), use_container_width=True, hide_index=True)
        else:
            st.info("No extraction rules yet for this supplier.")

        # --- Existing allocation rules ---
        st.markdown("#### Allocation Rules")
        allocation_rows = tier_c.list_allocation_rules(conn, tc_sp_id)
        if allocation_rows:
            st.dataframe(models_frame(allocation_rows, [
                "id", "extraction_rule_name", "member_id", "member_name", "entity_code", "allocation_type",
                "allocation_value", "contract_number", "product_line", "is_active",
            ]), use_container_width=True, hide_index=True)
        else:
            st.info("No allocation rules yet for this supplier.")

        st.divider()

        # --- Add / Edit Extraction Rule ---
        st.markdown("#### Add / Edit Extraction Rule")
        er_names = [r.rule_name for r in extraction_rows]
        er_mode = "Add new"
        if er_names:
            er_mode = st.radio("Extraction rule", ["Add new", "Edit existing"], horizontal=True, key="er_mode")
        er_existing = None
        if er_mode == "Edit existing" and er_names:
            er_edit_name = st.selectbox("Rule to edit", er_names, key="er_edit_name")
            er_existing = next(r for r in extraction_rows if r.rule_name == er_edit_name)

        d = er_existing.model_dump() if er_existing else {}
        # Widget keys are scoped to the rule being edited: Streamlit ignores
        # `value=` once a keyed widget has state, so shared keys would keep the
        # previous rule's (or the blank "Add new") values. (FINDINGS F13)
        ek = f"{tc_sp_id}_{er_existing.id if er_existing else 'new'}"
        # NOTE: these are plain top-level widgets, not st.form — the extraction_method
        # selection must trigger an immediate rerun so method-specific fields can
        # appear/disappear. This is a deliberate deviation from this file's other
        # (static) forms.
        er_rule_name = st.text_input("Rule Name *", value=d.get("rule_name", ""), key=f"er_rule_name_{ek}")
        er_sheet = st.text_input("Sheet name (blank = first sheet)", value=d.get("sheet_name") or "", key=f"er_sheet_{ek}")
        er_method = st.selectbox(
            "Extraction Method",
            META.extraction_methods,
            index=META.extraction_methods.index(d.get("extraction_method", "named_cell")),
            key=f"er_method_{ek}",
        )

        fields: dict = {}
        if er_method == "named_cell":
            fields["cell_address"] = st.text_input("Cell Address * (e.g. E8)", value=d.get("cell_address") or "", key=f"er_cell_{ek}")
        elif er_method in ("label_lookup", "label_range_sum"):
            fields["label_text"] = st.text_input("Label Text *", value=d.get("label_text") or "", key=f"er_label_{ek}")
            fields["label_match_mode"] = st.selectbox(
                "Label Match Mode", META.label_match_modes,
                index=META.label_match_modes.index(d.get("label_match_mode") or "exact"),
                key=f"er_match_mode_{ek}",
            )
            if fields["label_match_mode"] == "fuzzy":
                fields["label_match_threshold"] = st.number_input(
                    "Fuzzy Match Threshold (0-100)", min_value=0, max_value=100,
                    value=int(d.get("label_match_threshold") or 90), key=f"er_threshold_{ek}",
                )
            if er_method == "label_lookup":
                c1, c2 = st.columns(2)
                fields["value_row_offset"] = c1.number_input(
                    "Value Row Offset", value=int(d["value_row_offset"] if d.get("value_row_offset") is not None else 1),
                    key=f"er_row_off_{ek}")
                fields["value_col_offset"] = c2.number_input(
                    "Value Col Offset", value=int(d.get("value_col_offset") or 0), key=f"er_col_off_{ek}")
            else:
                fields["range_axis"] = st.selectbox(
                    "Range Axis", META.range_axes,
                    index=META.range_axes.index(d.get("range_axis") or "column"), key=f"er_axis_{ek}",
                )
                fields["stop_label_text"] = st.text_input(
                    "Stop Label Text (optional boundary)", value=d.get("stop_label_text") or "", key=f"er_stop_{ek}")
        elif er_method == "column_sum":
            col_spec_mode = st.radio("Specify column by", ["Index", "Letter"], horizontal=True,
                                     index=1 if d.get("column_letter") else 0, key=f"er_col_spec_{ek}")
            if col_spec_mode == "Index":
                fields["column_index"] = st.number_input(
                    "Column Index (0-based) *", min_value=0, value=int(d.get("column_index") or 0), key=f"er_col_idx_{ek}")
            else:
                fields["column_letter"] = st.text_input(
                    "Column Letter * (e.g. F)", value=d.get("column_letter") or "", key=f"er_col_ltr_{ek}") or None
            fields["header_row"] = st.number_input(
                "Header Row (0-indexed, sum starts below it)", min_value=0, value=int(d.get("header_row") or 0),
                key=f"er_hdr_row_{ek}")
        elif er_method == "formula":
            other_names = [n for n in er_names if n != d.get("rule_name")]
            if other_names:
                st.caption("Other rule names available to reference: " + ", ".join(other_names))
            fields["formula_expression"] = st.text_area(
                "Formula Expression * (e.g. \"Gross - Fees Withheld\")", value=d.get("formula_expression") or "",
                key=f"er_formula_{ek}")
        else:
            st.caption("manual_entry: no automated parsing — a human enters the confirmed total during Upload/Review.")

        er_is_sales = st.checkbox(
            "Is Sales Amount (feeds volume instead of fee_amount)",
            value=bool(d.get("is_sales_amount")), key=f"er_is_sales_{ek}",
        )
        er_sort_order = st.number_input("Sort Order", value=int(d.get("sort_order") or 0), key=f"er_sort_{ek}")

        if er_method == "formula" and fields.get("formula_expression"):
            show_notices(tier_c.formula_name_notices(er_names))

        er_save_col, er_del_col = st.columns([1, 1])
        if er_save_col.button("Save Extraction Rule", type="primary", key="er_save"):
            try:
                result = tier_c.save_extraction_rule(
                    conn, tc_sp_id,
                    ExtractionRuleInput(
                        extraction_method=er_method, rule_name=er_rule_name, sheet_name=er_sheet or None,
                        is_sales_amount=er_is_sales, sort_order=int(er_sort_order),
                        **{k: v for k, v in fields.items() if v not in ("",)},
                    ),
                    rule_id=er_existing.id if er_existing else None,
                )
                show_notices(result.notices)
                st.success(f"Extraction rule '{result.rule.rule_name}' saved.")
                st.rerun()
            except ValidationError as e:
                st.error(first_error(e))
            except CoreError as e:
                st.error(e.message)

        if er_existing and er_del_col.button("Delete Extraction Rule", key="er_delete"):
            try:
                tier_c.delete_extraction_rule(conn, tc_sp_id, er_existing.id)
                st.success(f"Deleted extraction rule '{er_existing.rule_name}'.")
                st.rerun()
            except CoreError as e:
                st.error(e.message)

        st.divider()

        # --- Add / Edit Allocation Rule ---
        st.markdown("#### Add / Edit Allocation Rule")
        if not er_names:
            st.info("Add an extraction rule first — allocation rules link to one by name.")
        else:
            ar_mode = "Add new"
            if allocation_rows:
                ar_mode = st.radio("Allocation rule", ["Add new", "Edit existing"], horizontal=True, key="ar_mode")
            ar_existing = None
            if ar_mode == "Edit existing" and allocation_rows:
                ar_options = {f"{r.extraction_rule_name} → {r.member_name}": r for r in allocation_rows}
                ar_pick = st.selectbox("Rule to edit", list(ar_options.keys()), key="ar_edit_pick")
                ar_existing = ar_options[ar_pick]

            ak = f"{tc_sp_id}_{ar_existing.id if ar_existing else 'new'}"  # see FINDINGS F13 note above
            ar_ext_name = st.selectbox(
                "Extraction Rule *", er_names,
                index=er_names.index(ar_existing.extraction_rule_name)
                if ar_existing and ar_existing.extraction_rule_name in er_names else 0,
                key=f"ar_ext_name_{ak}",
            )

            ar_member_id = ar_existing.member_id if ar_existing else None
            member_search = st.text_input("Search member by name or entity code *", key=f"ar_member_search_{ak}")
            if member_search and len(member_search) >= 2:
                members_found = members.search_members(conn, term=member_search, active_only=False, limit=20).items
                if members_found:
                    member_opts = {f"{m.name} ({m.entity_code})": m.id for m in members_found}
                    chosen_ar_member = st.selectbox("Select Member", list(member_opts.keys()), key=f"ar_member_choice_{ak}")
                    ar_member_id = member_opts[chosen_ar_member]
            elif ar_existing:
                st.caption(f"Currently: {ar_existing.member_name} ({ar_existing.entity_code})")

            ar_type = st.selectbox(
                "Allocation Type", META.allocation_types,
                index=META.allocation_types.index(ar_existing.allocation_type if ar_existing else "percent"),
                key=f"ar_type_{ak}",
            )
            ar_value = None
            if ar_type != "remainder":
                ar_value = st.number_input(
                    "Allocation Value (% for percent, $ for fixed_amount) *",
                    value=float(ar_existing.allocation_value or 0.0) if ar_existing else 0.0, key=f"ar_value_{ak}",
                )
            else:
                st.caption("Remainder: gets whatever is left after other rows for this extraction rule are applied.")
            c1, c2 = st.columns(2)
            ar_contract = c1.text_input("Contract Number (optional override)",
                                        value=(ar_existing.contract_number or "") if ar_existing else "", key=f"ar_contract_{ak}")
            ar_product = c2.text_input("Product Line (optional)",
                                       value=(ar_existing.product_line or "") if ar_existing else "", key=f"ar_product_{ak}")
            ar_active = st.checkbox("Active", value=ar_existing.is_active if ar_existing else True, key=f"ar_active_{ak}")

            show_notices(tier_c.allocation_notices(allocation_rows, AllocationRuleDraft(
                extraction_rule_name=ar_ext_name, allocation_type=ar_type, allocation_value=ar_value,
                rule_id=ar_existing.id if ar_existing else None,
            )))

            ar_save_col, ar_del_col = st.columns([1, 1])
            if ar_save_col.button("Save Allocation Rule", type="primary", key="ar_save"):
                if not ar_member_id:
                    st.error("Select a member first.")
                else:
                    try:
                        tier_c.save_allocation_rule(
                            conn, tc_sp_id,
                            AllocationRuleInput(
                                extraction_rule_name=ar_ext_name, member_id=int(ar_member_id), allocation_type=ar_type,
                                allocation_value=ar_value, contract_number=ar_contract or None,
                                product_line=ar_product or None, is_active=ar_active,
                            ),
                            rule_id=ar_existing.id if ar_existing else None,
                        )
                        st.success("Allocation rule saved.")
                        st.rerun()
                    except ValidationError as e:
                        st.error(first_error(e))
                    except CoreError as e:
                        st.error(e.message)

            if ar_existing and ar_del_col.button("Delete Allocation Rule", key="ar_delete"):
                tier_c.delete_allocation_rule(conn, tc_sp_id, ar_existing.id)
                st.success("Deleted allocation rule.")
                st.rerun()

with tab_aliases:
    st.subheader("Member Aliases")
    st.caption("Alternate names for members — used in Tier 2 matching (before fuzzy).")

    aliases = misc.list_aliases(conn, limit=200)
    if aliases:
        st.dataframe(models_frame(aliases, [
            "id", "member_name", "entity_code", "raw_name", "supplier_name", "approved_by", "approved_at",
        ]), use_container_width=True, hide_index=True)
    else:
        st.info("No aliases yet. They are created automatically during the Review process.")

    st.divider()
    st.markdown("#### Add Manual Alias")
    member_search = st.text_input("Search member by name or entity code", key="alias_member_search")
    if member_search and len(member_search) >= 2:
        members_found = members.search_members(conn, term=member_search, active_only=False, limit=20).items
        if members_found:
            member_opts = {f"{m.name} ({m.entity_code})": m.id for m in members_found}
            chosen_member = st.selectbox("Select Member", list(member_opts.keys()), key="alias_member_choice")
            chosen_member_id = member_opts[chosen_member]

            with st.form("alias_form"):
                alias_raw = st.text_input("Alias (alternate name) *")
                alias_jde = st.text_input("JDE Number (optional — ties alias to specific JDE)")
                alias_scope = st.selectbox("Scope", ["Global (all suppliers)", "Supplier-specific"])
                alias_supplier = None
                if alias_scope == "Supplier-specific":
                    sup_by_name = {s.name: s.id for s in suppliers.list_suppliers(conn)}
                    alias_supplier = sup_by_name[st.selectbox("Supplier", list(sup_by_name.keys()))]
                alias_saved = st.form_submit_button("Save Alias", type="primary")

            if alias_saved and alias_raw:
                try:
                    misc.create_alias(conn, AliasInput(
                        member_id=chosen_member_id, raw_name=alias_raw, jde_number=alias_jde or None,
                        supplier_profile_id=alias_supplier,
                    ))
                    st.success(f"Alias '{alias_raw}' saved.")
                except CoreError as e:
                    st.error(f"Error: {e.message}")
