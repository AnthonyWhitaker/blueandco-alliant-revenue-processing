# Legacy Streamlit UI

This is the original Streamlit interface (`ui/app.py` and `ui/pages/`). It was replaced by the React app in `../frontend`, served by the FastAPI service in `../api`. It was moved here, unchanged in behaviour, at the end of the migration (Phase 6).

It still works, against the same `core/` and `engine/` code and the same database:

```bash
make ui                                      # from revenue_engine/, needs `make install`
docker compose --profile legacy up legacy    # or in Docker, on :8502
```

`tests/test_legacy_ui.py` still runs it headlessly (Streamlit `AppTest`), so a change to `core/` that breaks it fails `make test`.

It is **not** in the production image (`.dockerignore` excludes `legacy/`). The last image that ran it is kept in the registry as `revenue-engine:streamlit-legacy` (see `../../DEPLOYMENT.md`, "Rollback").

## When it can be deleted

Delete this folder (plus `legacy/requirements.txt`, `tests/test_legacy_ui.py`, the `ui` Makefile target and the `legacy` compose service) once **all** of these are true:

1. The React deployment has run in the target Azure environment for a full processing cycle (at least one month-end), and no rollback to `:streamlit-legacy` was needed.
2. The people who use it day to day have signed off on the parity report (`../../PARITY.md`), including the intentional differences.
3. Nobody still relies on behaviour only the old UI has. As of the move there is none; check `../../FINDINGS.md` for anything that changed since.
4. The `revenue-engine:streamlit-legacy` image is kept (or deliberately retired) as the rollback path, independent of this folder.

Until then, keep it runnable. It is the reference for "how did the old app do this?".
