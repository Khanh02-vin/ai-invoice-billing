# Web MVP — Baseline Report

**Date:** 2026-11-07  
**Branch:** `exp1`  
**Status:** Baseline captured; implementation not yet included.

## Scope and safety boundary

The approved task is to complete the existing FastAPI/SQLite + React/Vite **Web MVP**. The current branch was already dirty before this work. At baseline, modified paths were `pyproject.toml`, `requirements.txt`, `src/app.py`, `src/extract/extractor.py`, `src/llm/base.py`, `src/models.py`, `src/routers/payments.py`, `src/store/repository.py`, `src/store/sql_repository.py`, `src/workers/extraction_worker.py`, and tests `test_dedup.py`, `test_invoice.py`, `test_organizations.py`, `test_payments_api.py`, `test_review.py`. Untracked user artifacts included Sprint reports, capability/gap docs, ADRs, migration/domain files, and tests. The pre-existing tracked diff summary was 645 insertions and 53 deletions across 15 files; staged diff was empty.

Those changes are user-owned and are not attributed wholesale to this task. Preserve all existing changes. No commit, push, deployment, remote-service change, dependency installation, or model download/replacement is authorized or performed.

## Verified starting capabilities

- **Invoice workflow:** image/PDF/text uploads, camera upload, extraction job polling, invoice list/filter, correction/review, and deletion exist. Extraction provenance is shown, but invoice original-file preview/download is not exposed in the reviewed UI.
- **Payment/transaction workflow:** transaction create/list/update/dismiss, amount/currency auto-match for a unique eligible decision, pending-receipt queue, explicit invoice upload for a selected pending transaction, and transaction attachment upload/list/download exist.
- **Matching gap:** manual invoice link/unlink API exists, but does not consistently reconcile transaction/invoice lifecycle states or cumulative partial allocations. No general candidate/manual re-match UI exists; matched invoice is commonly only an ID. Matching must never be represented as proof that payment occurred.
- **Purchases:** manual no-invoice purchase creation, photo-assisted draft, optional GPS, map/list, and deletion exist. No purchase edit or saved-photo retrieval UI was found.
- **Location:** legacy `location_*` transaction/purchase behavior is wired. ADR-0002 payment/merchant/context fields and SQLite backfill are present, but the transaction/purchase APIs and UI remain legacy-based. `evidence_refs` is not connected to attachment records. GPS is optional and must not be equated with merchant location.
- **History:** timeline/map and invoice detail are available; the History edit action appears to open the same read-only detail rather than invoice review. Several map/invoice navigation and coordinate-truthiness issues were identified in source review.
- **Frontend checks:** CI runs `npm ci` and `npm run build`; no frontend test framework/script is currently declared.
- **Scope exclusions:** No mobile/offline sync or PostGIS implementation is planned for this Web MVP absent evidence of necessity.

## Baseline checks

| Check | Command | Result |
|---|---|---|
| Full backend suite | `./.venv/bin/python -m pytest -q --tb=short` | **323 passed, 11 failed, 42 warnings** (62.00s) |
| Frontend production build | `(cd frontend && npm run build)` | **Passed**; Vite 5.4.21 built 45 modules (4.42s) |
| Initial incorrectly scoped frontend invocation | `npm run build` at repository root | Failed with ENOENT because root has no `package.json`; not a product failure, corrected by running from `frontend/`. |

Backend failures reproduce the Sprint 2.1 report:

- Nine `tests/test_gmail_lifecycle.py` async tests fail because the active pytest run has no async plugin (`async def functions are not natively supported`); `pytest.mark.asyncio` is unknown.
- Two `tests/test_purchase_memory.py` tests fail because real embedding results are `None`; prior verification found the configured ONNX model file present but ONNX Runtime unavailable in the active Python 3.14.4 environment.

No dependencies or environment settings were changed to mask these failures. Warnings remain reported by pytest; no assertions were weakened or tests skipped.

## Existing reports reconciled against source

`SPRINT_2_1_VERIFICATION_REPORT.md` records its own fresh outcome and residual risks; current baseline reproduces its full-suite counts. Earlier gap docs’ claims that production LLM fallback is unwired and that invoice-number absence always blocks receipts are stale relative to the later Sprint reports/current source. Treat capability/gap documents as dated snapshots, not authoritative implementation proof.

`docs/adr/ADR-0002-location-model.md` is accepted, but its richer fields are not end-to-end Web functionality yet. The implementation phase must test migration/backfill semantics and keep compatibility with legacy fields. The `evidence_refs` model/migration type and JSON round-trip need explicit coverage before use.

## Acceptance gates

- Each phase begins with tests that demonstrate the targeted behavior before implementation changes.
- Matching stays context-scoped (personal vs organization), automatic association remains unique-decision-only, allocations cannot exceed remaining balances, and linking/evidence does not assert payment verification.
- Location permission is optional, explicit/disclosed, and payment coordinates never become merchant-location proof by inference.
- Evidence ownership, retention, retrieval, and deletion are tested; original evidence and extraction provenance remain distinct.
- After each phase run focused backend/frontend checks and the production build; at completion run the full suite, API/workflow acceptance, build, and `git diff --check`.
- Environment-blocked tests and remaining risks are stated exactly; no commit, push, deployment, or remote changes.

## Current boundary

This report captures the baseline only. Implementation work and final acceptance results will be recorded separately in `WEB_MVP_EXECUTION_LOG.md` and `WEB_MVP_COMPLETION_REPORT.md`.
