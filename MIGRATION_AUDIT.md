# Migration Audit — Streamlit → React + FastAPI

Phase 0 deliverable. No application code has been changed. Everything below
comes from reading `revenue_engine/` at commit `896e441`. Anything I couldn't
determine from the repo is listed in **§9 Open questions**. Please answer
those before Phase 1.

---

## 0. Application context (inferred — please confirm)

| Item | What the repo says |
|---|---|
| What it does | Reconciles supplier remittance files (XLSX/CSV) for Alliant Purchasing Solutions. It matches customer rows to the Alliant member master (5-tier geo + fuzzy, plus a Tier 0 entity-code/account-ID fast path), aggregates revenue per entity code × earned month, resolves the Salesforce Payment ID from a ledger, and outputs a 4-tab Salesforce-ready XLSX. |
| Users | A small internal ops/finance team (the README names Brandi as the business owner). The workflow is upload → review unmatched → export, plus admin config. |
| Pages | 5: Home dashboard + Upload, Review, Export, Admin. Admin has 6 tabs. |
| Data | SQLite locally (`REVENUE_DB_PATH`) and Azure SQL in production (`AZURE_SQL_CONNECTION_STRING`, via pyodbc + msodbcsql18). 15 tables. Inputs are uploaded files only; there are no external APIs and no ML models. |
| Auth | **None.** Ingress is public unless `allowedIpRanges` is set in Bicep. `deploy.sh` ends with "Restrict access via IP allowlist or Easy Auth before sharing." |
| Long-running work | File processing, Account List load, Ledger load, and XLSX export (see §5). |
| Definition of done | Not stated → see §9. |

---

## 1. Page inventory

| Page | File | Purpose | Inputs | Outputs | User actions |
|---|---|---|---|---|---|
| Home | `ui/app.py` | Landing page, getting-started text, sidebar nav | none | 4 metrics: active members, suppliers, jobs, pending reviews | none (the nav comes from Streamlit's multipage sidebar) |
| Upload | `ui/pages/1_Upload.py` | Upload and process one supplier file | file (xlsx/xls/xlsm/csv), supplier, date received, optional Payment ID override | job summary (rows / matched / unmatched / revenue), payment ID + source, recent-jobs table (last 20) | upload file; change the inferred supplier/date; **Process File** |
| Review | `ui/pages/2_Review.py` | Resolve unmatched customers for a job | job selector (only jobs with pending items); per item: member search, member choice, create-facility form, alias checkbox, alias scope | one expander per pending item, sorted by total fee desc, showing the raw customer next to the best suggestion and its score | search members; **Create Facility & Match**; **Approve**; **Dismiss** |
| Export | `ui/pages/3_Export.py` | Preview and download the output | job selector (completed + needs_review); Payment ID if missing | 4 metrics; exceptions table (auto-expanded on needs_review); 4 preview tabs (Upload / Validation / Unmatched / JDE Mapping) | set Payment ID (writes the DB); **Generate Download File** → **Download** |
| Admin › Members | `4_Admin.py` | Load the member master | Account List.xlsx; "replace all" checkbox; single-member form | member count; sample of 50 | **Load Account List**; **Show sample members**; **Add Member** |
| Admin › Payment Ledger | `4_Admin.py` | Load the payment ledger | Ledger.xlsx; sheet name | ledger count; 30 most recent | **Load Ledger**; **Show recent ledger entries** |
| Admin › Suppliers | `4_Admin.py` | List and upsert supplier profiles | 12-field form | supplier table | **Save Supplier** (upsert by name) |
| Admin › Column Maps | `4_Admin.py` | Map supplier columns to canonical fields | supplier; sample file; single-mapping form | existing mappings; inferred mapping JSON | **Infer Column Map**; **Save Inferred Mappings**; **Save Mapping** |
| Admin › Tier C Rules | `4_Admin.py` | CRUD for extraction and allocation rules (pivot_allocation suppliers) | supplier; add/edit mode; dynamic method-specific fields; member search | extraction-rule table; allocation-rule table; live validation warnings (percent sum > 100, duplicate remainder, substring rule names) | Save/Delete extraction rule (cascades a rename into allocation rules; deletion is blocked while referenced); Save/Delete allocation rule |
| Admin › Aliases | `4_Admin.py` | View aliases and add one manually | member search; alias form (raw name, JDE, scope, supplier) | 200 aliases | **Save Alias** |

There is no edit/delete for suppliers, column mappings, members, or aliases. The only path is upsert/insert, and that is a parity constraint, not a gap the migration should fill (see §8).

---

## 2. Widget inventory

The **Recompute** column marks widgets whose value changes what gets queried or rendered, which Streamlit handles by rerunning the whole script. Display-only calls (`title`, `markdown`, `caption`, `divider`, `info/success/warning/error`, `metric`, `dataframe`, `json`, `expander`, `tabs`, `columns`, `container`) are counted, not listed.

### `ui/app.py`
- Display: `set_page_config`, `title`, `markdown`, `columns`, `metric`×8 (4 normal + 4 fallback), `sidebar.title/markdown×3/caption`.
- Caching: `@st.cache_resource get_db()` (see §6).
- No interactive widgets.

### `ui/pages/1_Upload.py`
| Widget | Key | Recompute? | Notes |
|---|---|---|---|
| `file_uploader` | — | **yes** | Gates the whole page; `st.stop()` when empty. |
| `selectbox` Supplier | — | **yes** | Defaults to the supplier inferred from the filename. |
| `date_input` Date Received (×2 branches) | — | **yes** | Defaults to the date parsed from the filename. |
| `text_input` Payment ID Override | — | no | Read only on submit. |
| `button` Process File | — | **yes (action)** | Disabled until file + supplier + date are set; runs `process_job` synchronously inside `st.spinner`. |
| Display | | | `dataframe`×2, `metric`×4, `info`×5, `caption`×5, `warning`×2, `error`×2, `success`, `stop`×2 |

### `ui/pages/2_Review.py` (widgets are repeated **per pending item**)
| Widget | Key | Recompute? | Notes |
|---|---|---|---|
| `selectbox` Select Job | — | **yes** | |
| `text_input` member search | `search_{id}` | **yes** | Runs a LIKE query on every rerun once ≥2 chars. |
| `selectbox` member choice | `choice_{id}` | **yes** | Includes a "None of these / add new facility" sentinel. |
| `form` new facility: `text_input`×5 + `form_submit_button` | `new_member_form_{id}`, `nm_name/ec/city/state/zip_{id}` | action | Only shown when the chosen member is None. |
| `checkbox` create alias | `alias_{id}` | no | Default True. |
| `radio` alias scope | `scope_{id}` | no | supplier / global / job_only. |
| `button` Approve | `approve_{id}` | action → `st.rerun()` | Disabled while no member is chosen. |
| `button` Dismiss | `dismiss_{id}` | action → `st.rerun()` | |
| Display | | | `expander` per item (all expanded), `markdown`×15, `container(border)` |

### `ui/pages/3_Export.py`
| Widget | Key | Recompute? | Notes |
|---|---|---|---|
| `selectbox` Select Job | — | **yes** | |
| `text_input` Payment ID | — | **yes (side effect)** | Any non-empty value is **written to the DB on every rerun**: two UPDATEs + commit. |
| `button` Generate Download File | — | action | Builds the XLSX to a temp file, then renders `download_button`. |
| `download_button` | — | action | |
| Display | | | `metric`×4, `expander` (exceptions), `tabs`×4, `dataframe`×5 |

### `ui/pages/4_Admin.py` (6 tabs, all rendered on every rerun)
| Tab | Widgets (key) | Recompute? |
|---|---|---|
| Members | `file_uploader` (`acct_upload`), `checkbox` replace-all, `button` Load, `button` Show sample; `form add_member_form` with 6 `text_input` + submit | Load/Show are actions; the form submits on action. |
| Ledger | `file_uploader` (`ledger_upload`), `text_input` sheet name, `button` Load, `button` Show recent | actions |
| Suppliers | `form supplier_form`: `text_input`×6, `selectbox`×4, `number_input`×2, `checkbox`, submit | submits on action |
| Column Maps | `selectbox` supplier (`colmap_supplier`) **yes**; `file_uploader` (`infer_file`); `button` Infer; `button` Save Inferred; `form colmap_form`: `text_input`, `selectbox`, `checkbox`, submit | supplier select recomputes |
| Tier C Rules | `selectbox` supplier (`tierc_supplier`) **yes**; `radio er_mode` **yes**; `selectbox er_edit_name` **yes**; `text_input er_rule_name/er_sheet/er_cell/er_label/er_stop/er_col_ltr`; `selectbox er_method` **yes**, `er_match_mode` **yes**, `er_axis`; `radio er_col_spec` **yes**; `number_input er_threshold/er_row_off/er_col_off/er_col_idx/er_hdr_row/er_sort`; `text_area er_formula`; `checkbox er_is_sales`; `button er_save`/`er_delete` → `rerun`; `radio ar_mode` **yes**; `selectbox ar_edit_pick` **yes**, `ar_ext_name` **yes**, `ar_type` **yes**; `text_input ar_member_search` **yes**; `selectbox ar_member_choice`; `number_input ar_value` **yes** (drives the live % warning); `text_input ar_contract/ar_product`; `checkbox ar_active`; `button ar_save`/`ar_delete` → `rerun` | Deliberately *not* an `st.form`, so method-specific fields appear and disappear live (see the code comment at line 402). |
| Aliases | `text_input alias_member_search` **yes**; `selectbox alias_member_choice`; `form alias_form`: `text_input`×2, `selectbox` scope, conditional `selectbox` supplier, submit | |

Totals in Admin: 26 `text_input`, 18 `selectbox`, 9 `number_input`, 5 `checkbox`, 3 `radio`, 1 `text_area`, 3 `file_uploader`, 10 `button`, 4 `form`, 4 `rerun`.

---

## 3. State inventory (`st.session_state`)

Implicit widget state (every keyed widget lives in `session_state`) is grouped by family. Unkeyed widgets hold state too, but nothing reads them across reruns except the widget itself.

| Key | Written by | Read by | Kind | Proposed new home |
|---|---|---|---|---|
| `conn` | `app.py` (from the `cache_resource` singleton) | every page: `st.session_state.get("conn") or init_db()` | infra handle, **shared across all users** | **Gone.** Becomes a per-request DB connection via a FastAPI dependency. |
| `_upload_inferred_for` | Upload | Upload | ephemeral UI ("have I already inferred for this filename?") | Gone: inference becomes a pure function call when a file is picked. |
| `_upload_guessed_supplier` | Upload | Upload | derived from the filename | API response from `POST /uploads/inspect` (or computed client-side) → form default |
| `_upload_guessed_date` | Upload | Upload | derived from the filename | same as above |
| `new_member_id_{item_id}` | Review (after Create Facility) | Review (overrides the chosen member) | wizard progress inside one review card | Local component state, or a Zustand `review` slice keyed by item id |
| `search_{id}`, `choice_{id}`, `alias_{id}`, `scope_{id}`, `nm_*_{id}` | widgets | Review | unsaved form state per item | Local component / form state per card. The search string feeds a Query key. |
| `infer_suggestions_{sp_id}` | Column Maps "Infer" | Column Maps "Save Inferred" | **unsaved draft of application data** | Mutation result held in a Zustand `columnMapDraft` slice (or local state) until saved; it must survive the supplier switch the way it does today. |
| `colmap_supplier`, `tierc_supplier` | widgets | Admin tabs | selected-item id | **URL param** (`/admin/column-maps?supplier=12`) |
| `er_mode`, `er_edit_name`, `ar_mode`, `ar_edit_pick` | widgets | Tier C editor | selected-item id / mode | **URL param** (`?rule=…`) or a route (`/admin/tier-c/:supplierId/extraction/:ruleId`) |
| `er_*` (≈18 field keys), `ar_*` (≈9 field keys) | widgets | Tier C editor | unsaved form state | Form library state (react-hook-form + Zod discriminated union on `extraction_method`) |
| `alias_member_search`, `alias_member_choice`, `ar_member_search`, `ar_member_choice` | widgets | Aliases / Tier C | search draft → selection | Local state; the search string feeds a Query key. Probably one shared `MemberPicker` component. |
| `acct_upload`, `ledger_upload`, `infer_file` | widgets | Admin | uploaded file bytes | Local `Dropzone` state → multipart POST |
| Unkeyed: Review/Export job selector | widget | page | selected-item id | **URL param** (`/review?job=42`, `/export/42`) |
| Unkeyed: Upload supplier/date/payment override | widget | page | unsaved form state | Form state |

**Conclusion:** there is almost no genuine cross-page client state. Nothing is shared between pages except `conn`. The Zustand footprint will be small: a Review per-item draft slice, a Column-Map inferred-draft slice, and maybe UI prefs such as the colour scheme. Most current `session_state` maps to URL params, Query cache, or form state. That matches the target architecture.

---

## 4. Logic vs. presentation split

| Module | Class | Notes |
|---|---|---|
| `engine/constants.py` | (a) logic | |
| `engine/normalizer.py` | (a) logic | Pure functions. Note: `parse_date_from_filename` is **defined twice** (lines 148 and 221); the second definition wins. |
| `engine/member_matcher.py` | (a)+(b) | Matching logic; loads its caches from the DB. |
| `engine/pivot_extractor.py` | (a) | Reads files and evaluates a whitelisted formula. |
| `engine/allocation_engine.py` | (a)+(b) | |
| `engine/file_reader.py` | (a)+(b) | |
| `engine/payment_resolver.py` | (a)+(b) | |
| `engine/member_loader.py` | (b) | |
| `engine/processor.py` | (a)+(b) | Orchestrator. Takes a `conn` and commits inside. |
| `engine/exporter.py` | (a)+(b) | Already exposes `export_to_dataframes` for previews. |
| `engine/db.py` | (b) | SQLite/MSSQL dialect shim (regex-rewrites `LIMIT`, upsert → `MERGE`, etc.). |
| `ui/app.py` | (c) + **(d)** | Dashboard COUNT queries are inline. |
| `ui/pages/1_Upload.py` | (c) + **(d)** | Inline: supplier list query, `revenue_job` INSERT, temp-file handling, recent-jobs query (duplicated twice). |
| `ui/pages/2_Review.py` | (c) + **(d)** | Inline: jobs-with-pending query, review-items query, member search query, `job_row` load. |
| `ui/pages/3_Export.py` | (c) + **(d)** | Inline: job list query, **Payment ID UPDATE ×2**, exceptions query, temp-file XLSX dance. |
| `ui/pages/4_Admin.py` | (c) + **(d), heavily** | Inline: ~20 SQL statements, including the supplier upsert, column-map insert-or-replace, all Tier C rule CRUD **with business rules** (rename cascade to allocation rules, delete-blocked-if-referenced, required-field rules per extraction method, percent-sum / duplicate-remainder / substring-name validation), alias insert. |

The good news: **no `engine/` module imports Streamlit** (`grep` confirms). Phase 1 is mostly about lifting category (d) out of `ui/`.

**Refactor backlog (category d → `core/` / `data/`):**
1. `dashboard_stats()`: 4 counts (app.py)
2. `list_suppliers()`, `upsert_supplier()` (Upload, Admin)
3. `create_job(supplier_id, file, date_received)`: INSERT `revenue_job` + file persistence (Upload)
4. `list_recent_jobs(limit)`, `list_jobs(status_in=…)`, `list_jobs_with_pending_reviews()` (Upload, Export, Review)
5. `list_pending_review_items(job_id)` with the suggestion join (Review)
6. `search_members(term, active_only)`: **three copies** with subtly different filters (Review filters `is_active=1`; Tier C and Aliases don't)
7. `set_job_payment_id(job_id, pid)` (Export)
8. `list_job_exceptions(job_id)` (Export)
9. Column map: `list_column_map`, `save_column_mapping`, `save_inferred_mappings`, and `infer_from_sample` (which wraps the inline `pd.read_excel/read_csv(nrows=5)`)
10. Tier C: `list_extraction_rules`, `save_extraction_rule` (validation + rename cascade), `delete_extraction_rule` (reference guard), `list_allocation_rules`, `save_allocation_rule`, `delete_allocation_rule`, `validate_allocation_rule` (warnings)
11. Aliases: `list_aliases(limit)`, `create_alias(...)`
12. Ledger / Members: `count_*`, `sample_members`, `recent_ledger_entries`

---

## 5. Long-running work (>~2 s, so it becomes async jobs)

| Operation | Where | Why it's slow | Recommendation |
|---|---|---|---|
| `process_job` | Upload | Reads the whole file; `MemberMatcher.load()` pulls every member, alias, and JDE row; fuzzy matching per unique customer (nationwide fuzzy over the full member list as a last resort); **one INSERT per row, plus a `SELECT @@IDENTITY` round trip per row on Azure SQL**. Bon Secours is 994 rows. | Async job: `POST /jobs` → 202 + id, `GET /jobs/{id}`. `revenue_job.status` (`uploaded → processing → completed/needs_review/failed`) already acts as a job-status table, so **no Redis/queue is needed**: run it in a background worker in-process (one replica, see §7). Poll; SSE isn't needed because there's no progress signal inside the pipeline today. |
| `load_account_list` | Admin › Members | Upserts thousands of member rows (MERGE per row on MSSQL) | Async job (or a synchronous call with a long timeout if you confirm the size is small) |
| `load_ledger_from_excel` | Admin › Ledger | Similar, smaller | Probably fine synchronously; measure first |
| `export_job` | Export | Builds 4 DataFrames + xlsxwriter | Synchronous streaming response is fine (`GET /jobs/{id}/export.xlsx`) |
| `apply_review_decision` | Review | Re-aggregates the whole job after every approval | Synchronous; measure on a large job |

I can't measure any of these: the sample files are gitignored and absent (§9).

---

## 6. Caching

| Cache | Protects | Replacement |
|---|---|---|
| `@st.cache_resource get_db()` (app.py) | One DB connection for the **whole process, shared by every browser session**. | Per-request connection (FastAPI `Depends`). The SQLite connection uses `check_same_thread=False`; pyodbc connections aren't thread-safe. Sharing one across concurrent users is a correctness risk today (one user's `commit()` commits another's half-done work), and FastAPI's threadpool would make it worse. |
| `MemberMatcher` in-memory caches | Member/alias/JDE lookups for one job | Unchanged (per job, not Streamlit) |
| `processor.match_cache` | Per-job dedupe of match calls | Unchanged |

There is **no `@st.cache_data` anywhere**, so there's no hidden memoised business logic to port. Query `staleTime` plus HTTP cache headers on reference data (suppliers, canonical fields) is all the caching needed.

---

## 7. Deployment inventory

| Item | Current state |
|---|---|
| Hosting | **Azure Container Apps** (not App Service). App `${namePrefix}-ui` (default `rev-ui`), env `rev-env` or a shared env from alliant-purchasing-solutions. |
| Scale | `minReplicas: 1, maxReplicas: 1`, 1 vCPU / 2 GiB |
| Registry | ACR, reused if one exists in the RG, otherwise `revreg######` (Basic, **admin user enabled**; creds passed to the Container App as a registry secret) |
| Image | `revenue-engine:latest`. **Only `:latest`, no versioned tags.** `deploy.sh` forces a revision restart because the tag never changes. |
| Build | `az acr build` (cloud build) from `revenue_engine/` as context |
| Pipeline | **None.** There is no `.github/workflows`, no `azure-pipelines.yml`. Deployment is a manual `make deploy` → `infra/deploy.sh`, which also runs Bicep and the schema-init + seed containers from the operator's machine (temporary SQL firewall rule). |
| IaC | `infra/main.bicep`: SQL server (new or shared) + `alliant_revenue` DB (S0), Log Analytics, Container Apps env (new or shared), Container App |
| Dockerfile | Single stage, `python:3.12-slim` (**unpinned digest/patch**), installs msodbcsql18 + unixodbc-dev, `pip install -r requirements.txt`, `COPY . .`, `EXPOSE 8501`, CMD `streamlit run ui/app.py --server.port=8501 --server.headless=true --server.address=0.0.0.0`. **Runs as root.** |
| Health check | Dockerfile `HEALTHCHECK curl -f http://localhost:8501/_stcore/health`. **However, `curl` is purged earlier in the same Dockerfile**, so this check can never pass. Container Apps ignores Docker `HEALTHCHECK`, and Bicep defines **no probes**, so Azure uses its default TCP probe on 8501. |
| Ingress | External, `targetPort: 8501`, HTTPS via the default `*.azurecontainerapps.io` FQDN; optional `allowedIpRanges` IP allowlist. No custom domain, VNet, or private endpoint. |
| `.dockerignore` | **Missing.** `COPY . .` sends `data/` (local SQLite DB) and any `.env` into the image if present in the build context. |
| docker-compose | One service, port 8501, `./data` volume, `REVENUE_DB_PATH=/app/data/revenue.db`. MSSQL service commented out. |
| Runtime env vars | `AZURE_SQL_CONNECTION_STRING` (Container App secret `sql-conn` → env; contains the SQL admin password), `REVENUE_DB_PATH` (local/compose only) |
| Deploy-time vars | `RESOURCE_GROUP`, `LOCATION`, `NAME_PREFIX`, `ACR_NAME`, `EXISTING_SQL_SERVER`, `EXISTING_ENVIRONMENT`, `SQL_ADMIN_PASSWORD` |
| Secrets store | Container App secrets (from Bicep params). No Key Vault, no managed identity. |
| Observability | Container stdout → Log Analytics (via the env). **No Application Insights.** |
| Storage | **None.** Uploaded files go to the container's `/tmp`, and `revenue_job.file_path` stores that temp path, so it is gone after any restart or revision. |

**Container strategy (preview of Phase 5):** single container is clearly right. It's one container today, single replica, no independent-scaling need. FastAPI serves the built SPA with an SPA fallback. Keeping port **8501** means no ingress/Bicep change. The health path will change (`/_stcore/health` is Streamlit-specific), and since no Azure probe references it, that change is safe.

---

## 8. Risk list (what Streamlit gives for free)

1. **File uploads** (4 places): Upload, Account List, Ledger, Column-map sample. These need multipart endpoints and size limits. Today the files land in `/tmp` and are never cleaned up.
2. **Download** (Export): `download_button` becomes a streaming `GET` with `Content-Disposition`. The filename is `"{original stem} Processed.xlsx"`, which parity requires.
3. **Session isolation is *not* real today**: one shared DB connection serves all sessions (§6). The API must not reproduce this.
4. **Rerun-on-change semantics** that encode behaviour:
   - The Tier C editor's live field show/hide by `extraction_method`, `label_match_mode`, column-spec mode, and allocation type becomes a discriminated-union form.
   - Live allocation warnings (percent sum > 100, duplicate remainder, substring rule names) are computed from *other saved rows plus the draft*. They should be a pure function in `core/`, exposed via a validate endpoint or mirrored client-side. I'd put it in `core/` and return the warnings from a `POST …/validate` so there's one source of truth.
   - Review member search queries on every keystroke, so it needs debouncing.
   - Export Payment ID **writes on every rerun** once non-empty; this becomes an explicit Save.
5. **`st.rerun()` call sites** (6): Review Approve/Dismiss, Tier C save/delete ×4. All become Query invalidations.
6. **`st.stop()` gating** (Upload ×2, Review, Export) becomes explicit empty states.
7. **Expanders all open on Review**: with many pending items, Streamlit renders N full forms. The React version should keep "everything visible, sorted by fee" as the default for parity; virtualisation can come later.
8. **Implicit "first item selected"**: every selectbox defaults to the first option (newest job, first supplier alphabetically). With URL params the default needs to be explicit.
9. **Error surfacing**: Upload re-`raise`s after `st.error`, so Streamlit shows a full traceback in the browser. The API will return problem details instead, which is an intentional deviation.
10. **No auth.** A Streamlit app is hard to script against. A REST API with unauthenticated write endpoints on a public FQDN is a **larger exposure** than today. I don't think we should ship Phase 5 without at least Container Apps Easy Auth (Entra ID) or the IP allowlist (see §9).
11. **No tests that run in CI.** `scripts/test_*.py` and `regression_test_guerbet.py` are print-style scripts that depend on gitignored real supplier files (`guerbet_example/`, `2026 Original/`). **Those files are not in this checkout**, so I can't run the parity baseline. `test_tier_c_engine.py` part 1 (synthetic fixture) is the only self-contained one.
12. **Streamlit isn't installed** in this environment (and the README notes it was broken before), so I can't run the legacy app for side-by-side parity yet.

Bugs found while auditing are recorded in `FINDINGS.md`, not fixed (per the working rules).

---

## 9. Open questions (please answer before Phase 1)

1. **CI/CD**: there is no pipeline, only `infra/deploy.sh` run by hand. Phase 5 says "update the existing pipeline in place". Should I (a) keep `deploy.sh` as the deploy path and just add frontend build/typecheck to the Dockerfile + Makefile, or (b) add a new GitHub Actions / Azure DevOps pipeline (which one)?
2. **Auth**: is the production app currently behind the IP allowlist, Easy Auth, or nothing? May I make Easy Auth (Entra ID) a Phase 5 prerequisite? Also, `resolved_by` is hard-coded to `"user"`/`"admin"` today; with auth it could record the real user. Is that wanted, or out of scope?
3. **Test data**: can you provide (or point me to) `guerbet_example/Uploads/Account List.xlsx` and the `2026 Original/2026 Complete` sample files locally? Without them the Phase 1 pytest contract can only cover synthetic fixtures. Is a small, anonymised, committed fixture set acceptable, given the "no mock data in committed code" rule? Test fixtures aren't app code, but please confirm.
4. **Uploaded file persistence**: async processing in-process can keep using local temp files (the same container handles POST and work). Durable storage (Azure Blob / Files) would be a scope expansion. OK to leave as-is and note it in `FINDINGS.md`?
5. **Parity of known quirks**: for the bugs in `FINDINGS.md` (e.g. the "job_only" alias scope creating a *global* alias), should the new UI reproduce the current behaviour exactly, or may it fix them? The working rules say don't fix silently, so I'm asking per item.
6. **Image tag**: keep `revenue-engine:latest` only, or may I additionally push a git-SHA tag (additive, enables rollback)?
7. **Definition of done**: is it feature parity on all 5 pages, then a production cut-over, then `legacy/`? Any deadline or pages that can be dropped (e.g. the Home dashboard)?
8. **Stack notes**: "Mantine DataTable" is the third-party `mantine-datatable` package (icflorescu), not part of Mantine core. Is that acceptable? The app has **no charts**, so `@mantine/charts` isn't needed. No objections otherwise; the target stack fits.
9. **Repo layout**: the app lives in `revenue_engine/`, and the Docker build context is that folder. I plan to put `api/ core/ data/ models/` inside `revenue_engine/`, put `frontend/` at `revenue_engine/frontend/` (so the single-image build context stays the same), and later move `ui/` to `revenue_engine/legacy/`. The prompt says `frontend/` at repo root. Which do you prefer?
