# Findings

Bugs and design issues in the existing code, found during the migration and
**not fixed** as part of it. Each one needs a decision: fix it separately,
fix it in the new UI, or reproduce it for parity.

| # | Area | Finding | Evidence | Severity |
|---|---|---|---|---|
| F1 | Review | Choosing alias scope **`job_only`** with "Create alias" ticked (the default) creates a **global** alias. `sp_id` is set to `None` for anything other than `supplier`/`global`, and `None` means global. | `engine/processor.py:792-794` | High: silently affects matching for every supplier in future jobs |
| F2 | Review | `job_only` decisions are still written to `jde_member_history`, so they persist into future jobs for that supplier regardless of scope. | `engine/processor.py:811-826` | Medium |
| F3 | Infra | One DB connection (`@st.cache_resource`) is shared by all user sessions. Concurrent users interleave transactions, and an Azure SQL idle disconnect is never recovered without a restart. | `ui/app.py:27-31` | Medium |
| F4 | Infra | The Dockerfile `HEALTHCHECK` uses `curl`, but `curl` is purged earlier in the same Dockerfile, so the check always fails. (Container Apps ignores it; no Azure probes are defined.) | `revenue_engine/Dockerfile:9-19,28-29` | Low |
| F5 | Infra | No `.dockerignore`: `COPY . .` can bake a local `data/revenue.db` or `.env` into the image. | `revenue_engine/Dockerfile:24` | Medium |
| F6 | Infra | The container runs as root; the base image is not pinned beyond `3.12-slim`; only the `:latest` tag is pushed (no rollback target). | `Dockerfile`, `infra/deploy.sh:109` | Low |
| F7 | Upload | Uploaded files are written to `/tmp` and never deleted, and `revenue_job.file_path` stores that path. After any restart or revision, jobs can't be reprocessed and `export_job`'s default output dir is invalid. | `ui/pages/1_Upload.py:111-114` | Medium |
| F8 | Tier C | Jobs for `pivot_allocation` suppliers land in `needs_review` (always, if `requires_manual_confirmation`), but they create no `member_review_item`s, so **they never appear on the Review page**, and no UI action moves them to `completed`. Export still allows download. | `engine/processor.py:508-510`, `ui/pages/2_Review.py:28-41` | Medium |
| F9 | Tier C | `manual_entry` extraction says "a human enters the confirmed total during Upload/Review", but no such input exists anywhere in the UI. | `ui/pages/4_Admin.py:461`, `engine/processor.py:419-425` | Medium |
| F10 | Export | The Payment ID text box writes to the DB (two UPDATEs + commit) on every rerun while it's non-empty, not on an explicit save. | `ui/pages/3_Export.py:52-63` | Low |
| F11 | Engine | `parse_date_from_filename` is defined twice in `normalizer.py`; the second definition silently wins. | `engine/normalizer.py:148, 221` | Low (verify which behaviour is intended) |
| F12 | Admin | Member search is implemented 3 times with different filters: Review excludes inactive members; Tier C allocation and Aliases include them. | `2_Review.py:118`, `4_Admin.py:588, 714` | Low |
| F13 | Tier C editor | *Suspected, needs runtime check:* editor widgets use static keys (`er_rule_name`, …) with `value=` taken from the selected rule. Streamlit ignores `value` once a keyed widget already has state, so switching "Rule to edit" may not repopulate the fields, and saving could write the previous rule's values onto the newly selected rule. | `ui/pages/4_Admin.py:406-467` | High if confirmed |
| F14 | Security | No authentication; the ingress is public unless `allowedIpRanges` is set. The ACR admin user is enabled, and the SQL admin login is used as the app's runtime credential. | `infra/main.bicep`, `infra/deploy.sh` | High for a REST API |
