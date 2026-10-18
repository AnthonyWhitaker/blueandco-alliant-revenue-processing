# Parity report: Streamlit → React

Phase 4 rebuilt every Streamlit page in `revenue_engine/frontend/`. Each page was checked against the running Streamlit app, both on the same seeded SQLite database (synthetic data from `tests/fixtures.py` plus `scripts/seed_*.py`), and has component tests.

**How it was checked:** in the browser, side by side, for Upload (process a file through to a result), Review (create a facility, then approve), Export (preview, then download headers and file name) and Admin › Tier C. The other Admin tabs are covered by component tests and spot checks.

**Not checked visually:** tablet width. The browser window couldn't be resized, so layouts only rely on Mantine's responsive props (grids collapse below `sm`/`md`, tables scroll sideways). See the checklist in the final report.

## Same behaviour, different mechanics (applies to every page)

| Streamlit | React |
|---|---|
| Whole-page rerun on every widget change | Only the affected query or field updates |
| `st.stop()` with an info box | An explicit empty state with a link to the next step |
| A Python traceback in the browser on errors | The API's problem message (in place, or as a toast for actions) |
| Selected job/supplier lost on reload | Kept in the URL (`?job=`, `?supplier=`, `?extractionRule=`, `?allocationRule=`) |
| Long work blocks the page (`st.spinner`) | Runs in the background and the page polls; you can leave and come back |

## Intentional differences, by page

### Dashboard (Home)
- The welcome paragraph is shortened to one line. The four counts and the "Getting started" steps are kept, now as links.
- Pending reviews links to Review when it is above zero.

### Upload
- Processing is asynchronous (`POST /jobs` → 202). The result panel follows the job in `?job=` and survives a reload. Legacy showed the result only until the next rerun.
- Each Recent jobs row opens that job's result.
- When the Payment ID can't be resolved, the page links to Export, where it can be set. Legacy said to "reprocess or set it directly on the job".
- The date uses the browser's native date input.

### Review
- Items are cards, all expanded, highest fee first, as the legacy expanders.
- The member search is one search-as-you-type picker, replacing the legacy text box plus selectbox. "None of these / add new facility" is a button rather than a dropdown entry.
- **A facility created for an item is just the current selection.** In legacy it overrode any later choice until reload. You can also go back to the engine's suggestion.
- The alias scope options are labelled "This supplier / All suppliers / This job only", with explanations. They send the same values (`supplier` / `global` / `job_only`). `job_only` now really saves no alias (F1/F2, fixed in the engine).
- Approve and Dismiss remove the card immediately. If the call fails, the queue is refetched and the card comes back with the error.
- A half-finished decision (picked member, created facility, alias settings) survives leaving the page.

### Export
- One "Download workbook" button replaces "Generate Download File" followed by a second download button. The file name is unchanged: `"{original stem} Processed.xlsx"`.
- A zero count shows `0` rather than `—`. Legacy used `value or "—"`, which hid zeros.
- The preview tabs show their row counts. The Unmatched tab links to Review when items are still pending.
- The Payment ID is saved explicitly (F10) and needs_review jobs can be signed off (F8), as in the updated legacy page.

### Admin › Members / Payment Ledger
- Imports run in the background. The status shows in place and is still there after leaving the tab or reloading (the `imports` slice, kept for the browser tab's session).
- "Replace all existing members" asks for confirmation and explains what it deletes (F23).
- "Show sample" / "Show recent" toggle the table open and closed.

### Admin › Suppliers
- A table with an **Edit** button per supplier, opening a pre-filled form, replaces the never-pre-filled "Add / Update" form that upserted by name (**F22**: legacy reset untouched fields).
- Fee rate and the four fuzzy-match thresholds can now be edited, in a collapsed section. Legacy couldn't edit them, and a full update has to send them anyway.

### Admin › Column Maps
- Inferred mappings can be discarded, and they are cleared after saving. Legacy left them on screen, so a second click saved them again.

### Admin › Tier C Rules
- Each rule row has an Edit button. The rule being edited is in the URL. Deletes ask for confirmation.
- Method-specific fields appear live, as in legacy, with inline validation before the request goes out.
- Allocation warnings (percent total, duplicate remainder) come live from `POST …/allocation-rules/validation`. The formula-name warning is computed in the browser and mirrors `core.tier_c.formula_name_notices`.
- `manual_entry` shows a note that there is nowhere to enter the confirmed total yet (F9).

### Admin › Aliases
- The Supplier select appears as soon as "Supplier-specific" is chosen (**F21**: in legacy it could silently attach the alias to the first supplier).
- The list caption says the list is ordered by member name (the first 200).

## Found while checking parity
- **Upload size limit:** Streamlit allows 200 MB per file, while the API defaulted to 50 MB. The API default is now 200 MB (`MAX_UPLOAD_MB`).
- **Polling in background tabs:** job and import polling now continues while the tab is hidden, so lists refresh when work finishes.
- **A fetch wrapper in the test browser corrupted uploads.** Something injected into the page (not app code, most likely a browser extension) replaces `window.fetch` and reads request bodies as text. That mangles binary uploads sent as `fetch(Request)`, which is how the typed API client calls fetch. With the native `fetch` restored, uploads were byte-identical. Worth confirming in a clean browser profile; see the checklist.
