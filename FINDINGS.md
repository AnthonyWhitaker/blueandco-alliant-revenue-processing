# Findings

Bugs and design issues in the existing code found during the migration. On
2026-10-15 the user approved fixing them (rather than reproducing them for
parity). The **Status** column records what has actually been done.

| # | Area | Finding | Evidence | Severity | Status |
|---|---|---|---|---|---|
| F1 | Review | Choosing alias scope **`job_only`** with "Create alias" ticked (the default) creates a **global** alias. | `engine/processor.py` `apply_review_decision` | High | **Fixed** (8d6df01), test `test_job_only_scope_leaves_no_lasting_mapping` |
| F2 | Review | `job_only` decisions were still written to `jde_member_history`, so they carried into future jobs. | same | Medium | **Fixed** (8d6df01) |
| F3 | Infra | One DB connection (`@st.cache_resource`) is shared by all sessions; Azure SQL idle disconnects are never recovered. | `ui/app.py` | Medium | **Fixed in the API** (per-request connection). The legacy app still has it. |
| F4 | Infra | The Dockerfile `HEALTHCHECK` uses `curl`, which the same Dockerfile purges. | `Dockerfile` | Low | Open (Phase 5) |
| F5 | Infra | No `.dockerignore`: `COPY . .` can bake `data/revenue.db` / `.env` into the image. | `Dockerfile` | Medium | Open (Phase 5) |
| F6 | Infra | Runs as root; base image not pinned; only `:latest` is pushed (no rollback target). | `Dockerfile`, `infra/deploy.sh` | Low | Open (Phase 5) |
| F7 | Upload | Uploaded files went to `/tmp` and were never cleaned up; `revenue_job.file_path` points at them. | `ui/pages/1_Upload.py` (old) | Medium | **Partly fixed**: files now go to `REVENUE_UPLOAD_DIR` (default `data/uploads`). They are still lost when an Azure revision restarts, because there's no persistent volume. Durable storage is a scope decision. |
| F8 | Tier C | `pivot_allocation` jobs land in `needs_review` with no review items, so they never appear on Review and can't be completed. | `engine/processor.py:508-510` | Medium | **Fixed**: `confirm_job` (`POST /api/jobs/{id}/confirmation`, plus a button on the legacy Export page) |
| F9 | Tier C | `manual_entry` says "a human enters the confirmed total during Upload/Review", but no such input exists. | `engine/processor.py:419-425` | Medium | Open: needs a product decision on where the total is entered |
| F10 | Export | The Payment ID box wrote to the DB on every rerun. | `ui/pages/3_Export.py` (old) | Low | **Fixed**: explicit Save (legacy) / `PATCH /api/jobs/{id}` |
| F11 | Engine | `parse_date_from_filename` was defined twice; the surviving copy returned impossible dates (crashing Upload). | `engine/normalizer.py` | Low | **Fixed** (3175622) |
| F12 | Admin | Member search had three copies with different active/inactive filters. | old UI pages | Low | **Fixed**: one `core.members.search_members(active_only=…)`; each call site keeps its previous filter |
| F13 | Tier C editor | **Confirmed by test:** "Edit existing" never loaded the selected rule's values, and saving after changing only some fields silently reset `sheet_name`, `is_sales_amount` and `sort_order` (e.g. `Summary/1/5` → `None/0/0`). The allocation editor had the same pattern. | `ui/pages/4_Admin.py` (old) | **High**: silently corrupts Tier C config | **Fixed** in legacy (600c291), test `test_tier_c_edit_prefills_and_preserves_fields` |
| F14 | Security | No authentication; public ingress unless `allowedIpRanges` is set; ACR admin user enabled; the SQL admin login is the app's runtime credential. | `infra/` | High for a REST API | Open: **auth decision needed before Phase 5** |
| F15 | Admin | The Column Map form couldn't map `entity_code_hint` / `account_id_hint` or `contains:` patterns, so Tier A suppliers could only be onboarded by seed script. | old `4_Admin.py` | Medium | **Fixed** (all canonical fields offered; `contains:` documented) |
| F16 | Engine / Azure SQL | *Suspected, not runtime-verified (no SQL Server here):* `_build_review_items` selects `jde_number, customer_name, city, state, zip` while grouping only by `COALESCE(jde_number, customer_name)`. SQLite allows this; **SQL Server rejects it**. If so, any job with an unmatched customer fails on Azure SQL. The old Review page's job query had the same pattern; the new `core/` query is portable. | `engine/processor.py` `_build_review_items` | **High if confirmed** | Open: verify against the real Azure SQL DB (or a local SQL Server container), then fix with `MIN()`/`MAX()` aggregates |
| F17 | Engine | Output aggregation groups by raw `process_date`, not earned month, so a PEC gets one output row per distinct date in the same period. The README says "one row per entity code per earned period". Totals are unaffected. | `engine/processor.py` `_aggregate_output` | Unknown: depends on whether Salesforce upload expects one row per PEC × month | Open: needs business confirmation. Current behaviour pinned in `test_pipeline_aggregates_per_pec_and_process_date`. |
