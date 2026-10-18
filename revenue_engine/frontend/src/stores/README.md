# Client state architecture

Where each piece of state that used to live in Streamlit's `st.session_state` lives now.
The source inventory is `MIGRATION_AUDIT.md` §3 at the repo root.

## The four homes

| Home                                              | Holds                                                                                                       | Rule of thumb                                                                         |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **TanStack Query** (`src/queries/keys.ts`)        | Anything that came from the API and can be fetched again.                                                   | Never copy it into Zustand or `useState`. No `useEffect` + `fetch` + `setState`.      |
| **URL search params** (`src/lib/searchParams.ts`) | Selected-item ids and filters.                                                                              | If a reload, Back, or a pasted link should land on the same view, it goes in the URL. |
| **Zustand** (`useAppStore`, this folder)          | Client state that must outlive the component that shows it: drafts, wizard progress, ids of work in flight. | One store, one slice per domain. Subscribe with selectors.                            |
| **Local component / form state**                  | Everything else: field values while typing, search boxes inside pickers, picked files, open/closed toggles. | The default. Promote it only when it needs to outlive its component.                  |

## Every `session_state` key and where it went

**Legacy key** is the Streamlit key, with `{id}` for the review item and `{sp_id}` for the supplier.

### Infrastructure

| Legacy key | Was                                                                   | New home                                                               |
| ---------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `conn`     | A DB connection created once in `app.py` and **shared by every user** | **Removed.** The API opens one connection per request (`api/deps.py`). |

### Upload (`1_Upload.py`)

| Legacy key                                               | Was                                                                               | New home                                                                                                                                                                                                       |
| -------------------------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_upload_inferred_for`                                   | "Have I already guessed for this filename?" (prevents re-guessing on every rerun) | **Removed.** Query caches the guess by filename, so a repeat lookup is free.                                                                                                                                   |
| `_upload_guessed_supplier`, `_upload_guessed_date`       | Supplier and date guessed from the filename                                       | **Query** `fileNameInference(fileName)` → `GET /api/file-name-inference`. The form uses it as default values only.                                                                                             |
| _(unkeyed)_ file uploader                                | The picked file                                                                   | **Local** state (a `File` object; not serialisable, upload page only).                                                                                                                                         |
| _(unkeyed)_ supplier, date received, Payment ID override | Unsaved form                                                                      | **Local form** state.                                                                                                                                                                                          |
| _(unkeyed)_ the result shown after "Process File"        | Lost on any rerun or reload                                                       | **URL** `/upload?job=42` + **Query** `jobs.detail(42)`, polled until the job finishes. `POST /api/jobs` answers 202 with the job; the page navigates to its id. _(Improvement: the result survives a reload.)_ |

### Review (`2_Review.py`)

| Legacy key                                                                   | Was                                                                               | New home                                                                                                                                                    |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| _(unkeyed)_ "Select Job to Review"                                           | Selected job                                                                      | **URL** `/review?job=42`. When absent, the page replaces the URL with the first job that has pending items, as the selectbox defaulted to the first option. |
| `new_member_id_{id}`                                                         | Wizard step: "I just created a facility for this item" (outlived page navigation) | **Zustand `review`** `recordCreatedMember(itemId, member)` → `selection.kind = 'picked', created: true`.                                                    |
| `choice_{id}`                                                                | Member picked from search results, or the "None of these" sentinel                | **Zustand `review`** `pickReviewMember` / `rejectReviewCandidates`. Untouched means "use the engine's suggestion" (`resolveReviewMemberId`).                |
| `alias_{id}`                                                                 | Create-alias checkbox (default on)                                                | **Zustand `review`** `setReviewCreateAlias`.                                                                                                                |
| `scope_{id}`                                                                 | Alias scope radio (default `supplier`)                                            | **Zustand `review`** `setReviewAliasScope`.                                                                                                                 |
| `search_{id}`                                                                | Member search box, re-queried on every rerun at 2+ characters                     | **Local** (debounced) → **Query** `members.search({ search, limit })`.                                                                                      |
| `nm_name_{id}`, `nm_ec_{id}`, `nm_city_{id}`, `nm_state_{id}`, `nm_zip_{id}` | New-facility form fields                                                          | **Local form** state, prefilled from the item. Submitting calls `POST /api/members`, then `recordCreatedMember`.                                            |
| _(the queue itself)_                                                         | Re-queried on every rerun                                                         | **Query** `jobs.reviewItems(jobId)`.                                                                                                                        |

### Export (`3_Export.py`)

| Legacy key                                  | Was                                     | New home                                                                                                           |
| ------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| _(unkeyed)_ "Select Job"                    | Selected job                            | **URL** `/export?job=42`, defaulting to the first exportable job, as the selectbox did.                            |
| _(unkeyed)_ Payment ID text box             | **Wrote to the DB on every rerun** (F3) | **Local form** state, saved only by an explicit "Save" (`PATCH /api/jobs/{id}`).                                   |
| _(unkeyed)_ the generated workbook          | Temp file + `download_button`           | **Not state.** "Download" fetches `GET /api/jobs/{id}/export` and hands the blob to the browser.                   |
| _(unkeyed)_ the exceptions and preview tabs | Re-queried on every rerun               | **Query** `jobs.exceptions(id)`, `jobs.preview(id)`. Which tab is open stays local (or in the URL hash if wanted). |

### Admin (`4_Admin.py`)

The six `st.tabs` are already routes (`/admin/members`, …, `/admin/aliases`), so the open tab is in the URL.

| Legacy key                                                                                                                                                                                                                                         | Was                                                                              | New home                                                                                                                                                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `acct_upload`, `ledger_upload`, `infer_file`                                                                                                                                                                                                       | Picked files                                                                     | **Local** (Dropzone) → multipart POST.                                                                                                                                                  |
| _(unkeyed)_ "Replace all", ledger sheet name                                                                                                                                                                                                       | Upload options                                                                   | **Local form** state.                                                                                                                                                                   |
| _(new: no legacy key)_ the running member or ledger import                                                                                                                                                                                         | Imports ran inside the request, so there was nothing to remember                 | **Zustand `imports`** `activeImports[kind] = taskId` (persisted per tab, see below). Status and result come from **Query** `task(taskId)`.                                              |
| _(unkeyed)_ "Show sample" members, "Show recent" ledger                                                                                                                                                                                            | Buttons that loaded a table once                                                 | **Query** `members.search(…)`, `ledgerEntries.list({ limit })`, enabled by a **local** toggle.                                                                                          |
| `add_member_form` fields, `supplier_form` fields, `colmap_form` fields, `alias_form` fields                                                                                                                                                        | Unsaved forms                                                                    | **Local form** state.                                                                                                                                                                   |
| `colmap_supplier`                                                                                                                                                                                                                                  | Selected supplier on Column Maps                                                 | **URL** `/admin/column-maps?supplier=12`.                                                                                                                                               |
| `infer_suggestions_{sp_id}`                                                                                                                                                                                                                        | Inferred mappings awaiting "Save Inferred Mappings"; survived switching supplier | **Zustand `columnMapDraft`** `inferredMappings[supplierId]`. Cleared after a successful save.                                                                                           |
| `tierc_supplier`                                                                                                                                                                                                                                   | Selected supplier on Tier C Rules                                                | **URL** `/admin/tier-c?supplier=12`. Changing it clears both rule params in the same navigation.                                                                                        |
| `er_mode` + `er_edit_name`                                                                                                                                                                                                                         | "Add new" / "Edit existing" + which rule (**by name**)                           | **URL** `extractionRule=new` or `extractionRule=<rule id>`. Using the id, not the name, is what fixes F13. Absent means `new`.                                                          |
| `ar_mode` + `ar_edit_pick`                                                                                                                                                                                                                         | Same, for allocation rules                                                       | **URL** `allocationRule=new` or `allocationRule=<rule id>`.                                                                                                                             |
| `er_rule_name`, `er_sheet`, `er_cell`, `er_label`, `er_stop`, `er_col_ltr`, `er_method`, `er_match_mode`, `er_axis`, `er_col_spec`, `er_threshold`, `er_row_off`, `er_col_off`, `er_col_idx`, `er_hdr_row`, `er_sort`, `er_formula`, `er_is_sales` | Extraction-rule editor fields                                                    | **Local form** state, validated per `extraction_method`. The form is keyed by supplier + rule, so switching resets it (the F13 fix).                                                    |
| `ar_ext_name`, `ar_type`, `ar_value`, `ar_contract`, `ar_product`, `ar_active`                                                                                                                                                                     | Allocation-rule editor fields                                                    | **Local form** state. The live "% allocated" warning comes from the cached rule list plus the field value. Server-side checks run as a mutation (`POST …/allocation-rules/validation`). |
| `ar_member_search` + `ar_member_choice`, `alias_member_search` + `alias_member_choice`                                                                                                                                                             | Member picker: search, then choose                                               | **Local** state inside one shared `MemberPicker` component. Search → **Query** `members.search`.                                                                                        |

## Zustand slices

`useAppStore` = `review` + `columnMapDraft` + `imports`, wrapped in `devtools` (dev builds only) and `persist`.
Each slice file opens with a comment saying what it owns, who writes it, and which key it replaces.

| Slice                    | State                                                               | Actions                                                                                                                                  | Persisted             |
| ------------------------ | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| `reviewSlice.ts`         | `reviewDrafts: Record<itemId, ReviewDraft>`                         | `pickReviewMember`, `recordCreatedMember`, `rejectReviewCandidates`, `setReviewCreateAlias`, `setReviewAliasScope`, `discardReviewDraft` | No                    |
| `columnMapDraftSlice.ts` | `inferredMappings: Record<supplierId, InferredMapping[]>`           | `setInferredMappings`, `discardInferredMappings`                                                                                         | No                    |
| `importsSlice.ts`        | `activeImports: { member_import?: taskId, ledger_import?: taskId }` | `trackImport`, `forgetImport`                                                                                                            | Yes, `sessionStorage` |

How to use the store:

- Read through the narrow hooks in `hooks.ts` (`useReviewDraft(id)`, `useInferredMappings(id)`, `useActiveImport(kind)`), or `useAppStore((s) => s.x)`. Use `useShallow` when selecting several fields. Never destructure the whole store.
- Select actions one at a time (`useAppStore((s) => s.pickReviewMember)`). Action references never change, so this costs no re-renders.
- Selector defaults must be module-level constants (`EMPTY_REVIEW_DRAFT`). A fresh `{}` in a selector re-renders forever in Zustand 5.

### Persistence

Only `activeImports` is persisted. Imports now run in the background, and a reload mid-import should still show how it ended.

- Storage is `sessionStorage` (one browser tab), under key `revenue-engine:client-state`.
- The schema is versioned (`PERSIST_VERSION = 1`). `migratePersistedState` keeps valid version-1 data and drops anything else.
- When the persisted shape changes, bump the version and add a migration case.
- A task id that 404s after an API restart (tasks are held in memory) is cleared with `forgetImport`.

Review drafts and column-map drafts reset on reload, as they did in Streamlit.
The colour scheme is persisted by Mantine's own `localStorage` manager, not by this store.

## Query cache

Keys come from `src/queries/keys.ts`. They are hierarchical, so invalidating a prefix (`jobs.all`) refreshes every list, detail and sub-resource under it.

Defaults (`src/lib/queryClient.ts`):

- `staleTime` is 30 s.
- 4xx responses are never retried.
- Failed mutations show a toast unless the mutation sets `meta.handlesErrors` (forms do, to show field errors inline).
- The `meta` query (option lists for selects) will use `staleTime: Infinity`; the lists only change on deploy, and the API sends a one-hour `Cache-Control`.

| Mutation                                            | Then                                                                                                                                                                                                                                                             |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /jobs` (upload)                               | Navigate to `?job=id`; invalidate `jobs.all`, `dashboard`; poll `jobs.detail(id)` while the status is `uploaded`/`validating`/`validated`/`processing`.                                                                                                          |
| `PATCH /jobs/{id}` (Payment ID)                     | Put the returned job into `jobs.detail(id)`; invalidate `jobs.all`.                                                                                                                                                                                              |
| `POST /jobs/{id}/confirmation`                      | Invalidate `jobs.all`, `dashboard`.                                                                                                                                                                                                                              |
| `POST /review-items/{id}/approval` and `/dismissal` | **Optimistic:** remove the item from `jobs.reviewItems(jobId)`, roll back on error. Then put the returned job into `jobs.detail(jobId)`, invalidate `jobs.all` and `dashboard`, invalidate `aliases.all` when an alias was saved, and call `discardReviewDraft`. |
| `POST /members`                                     | Invalidate `members.all`, `dashboard`.                                                                                                                                                                                                                           |
| `POST /member-imports`, `/ledger-imports`           | `trackImport`; poll `task(id)` until `succeeded`/`failed`; on success invalidate `members.all` + `dashboard`, or `ledgerEntries.all`.                                                                                                                            |
| `POST /suppliers`, `PUT /suppliers/{id}`            | Invalidate `suppliers.all`, `dashboard`.                                                                                                                                                                                                                         |
| `POST …/column-mappings`, `…/column-mappings/bulk`  | Invalidate `suppliers.columnMappings(id)`. A bulk save also calls `discardInferredMappings(id)`.                                                                                                                                                                 |
| `POST …/column-mappings/inference`                  | Not cached: the result goes to `setInferredMappings(id, result)`.                                                                                                                                                                                                |
| Extraction rule save / delete                       | Invalidate `suppliers.extractionRules(id)` **and** `suppliers.allocationRules(id)`, because a rename cascades to allocation rules.                                                                                                                               |
| Allocation rule save / delete                       | Invalidate `suppliers.allocationRules(id)`.                                                                                                                                                                                                                      |
| `POST /aliases`                                     | Invalidate `aliases.all`.                                                                                                                                                                                                                                        |

## URL params

Names are in `searchParamNames` (`src/lib/routes.ts`) and are read with `useSearchParamState(name, codec)`. Invalid values (`?job=abc`) read as absent.

- **Selections push a history entry.** Back returns to the previous job or supplier.
- **Automatic defaults replace the entry** (`{ replace: true }`), so Back is not trapped.
- **A parent change clears its children in one navigation** (`usePatchSearchParams({ supplier, extractionRule: null, allocationRule: null })`).

| Route                | Params                                                                           |
| -------------------- | -------------------------------------------------------------------------------- |
| `/upload`            | `job` (the job just submitted)                                                   |
| `/review`            | `job`                                                                            |
| `/export`            | `job`                                                                            |
| `/admin/column-maps` | `supplier`                                                                       |
| `/admin/tier-c`      | `supplier`, `extractionRule` (`new` or an id), `allocationRule` (`new` or an id) |

## Intentional differences from Streamlit

1. **A created facility no longer overrides later choices.** In Streamlit, once `new_member_id_{id}` was set it beat whatever the reviewer picked afterwards, with no way to undo it short of a reload. Now it is just the current selection, and picking another member replaces it.
2. **Inferred mappings are cleared after they are saved.** Streamlit kept them, and the "Save Inferred Mappings" button stayed visible, so saving again re-wrote the same rows.
3. **Selections and processing results survive a reload and can be shared as links** (all the URL rows above).
