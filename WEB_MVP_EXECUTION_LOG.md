# Web MVP Execution Log

**Branch:** `exp1`  
**Start date:** 2026-11-07  
**Boundary:** No commits, pushes, deployment, remote changes, dependency installation, model download/replacement, or destructive Git operations. Existing dirty-worktree changes are preserved as user-owned.

## Phase 0 — Baseline and scope

- Rechecked branch and status before edits: `exp1`; pre-existing staged diff empty; 15 modified tracked files and the user’s untracked sprint/docs/migration/domain/test artifacts were present. See `WEB_MVP_BASELINE_REPORT.md` for path inventory and initial diff statistics.
- Backend baseline `./.venv/bin/python -m pytest -q --tb=short`: **323 passed, 11 failed, 42 warnings**. Nine Gmail async tests fail because the active pytest environment does not activate an async plugin; two purchase-memory real-model tests fail with no embedding because ONNX Runtime is unavailable. This matches Sprint 2.1’s documented environmental blockers.
- Frontend baseline `(cd frontend && npm run build)`: **passed**, Vite 5.4.21, 45 modules.
- No dependencies or environment settings were changed. The accidental first `npm run build` at repository root failed with ENOENT (no root `package.json`); rerunning in `frontend/` succeeded.
- Current-source conclusions and phase acceptance gates recorded in `WEB_MVP_BASELINE_REPORT.md`.

## Phase 1 — Sprint 2.1 verification closure

- Reconciled the existing `SPRINT_2_1_VERIFICATION_REPORT.md` with a fresh full-suite run. Counts and failure groups match: **323 passed, 11 failed, 42 warnings**.
- Kept Sprint 2.1 classified as **PARTIALLY VERIFIED**, not fully green. No repeat edits were needed for that already-completed task.
- Carry-forward risks: concurrent SQLModel duplicate insert race; malformed raw JSON decoder behavior; missing-original camera retry fallback lacks dedicated regression; Gmail async plugin and ONNX Runtime environment blockers.
- No changes made to the Sprint 2.1 report or its previously dirty code paths during closure.

## Phase 2 — Sprint 3 transaction/invoice matching

- Implemented tenant-scoped invoice candidate review, manual link/unlink allocation reconciliation, cumulative balance guards, and linked transaction edit safeguards in the existing FastAPI/repository paths.
- Added pending-transaction matching actions and ambiguity/payment-verification disclaimers in the invoice UI; History edit now opens invoice review, and Ledger matched-invoice references are navigable.
- Verification: `./.venv/bin/python -m pytest -q tests/test_payments_api.py --tb=short`: **23 passed, 4 warnings**. `(cd frontend && npm run build)`: **passed**, Vite 5.4.21, 45 modules. `git diff --check`: **passed**.
- Automatic payment verification remains unchanged: a match/link or amount alone does not verify payment. Broader user changes in these dirty files are not attributed to this phase.

## Phase 3 — Sprint 4 location model

- Completed ADR-0002 invoice API validation and persistence for legacy location plus payment-time/device, merchant, purchase-context, evidence-reference, and user-confirmation fields. Patch semantics and organization/user scope remain intact; coordinate pairs/ranges, accuracy, confidence, context values, and zero coordinates are covered.
- Updated Purchases and Ledger to distinguish device/payment-time location from merchant location, with explicit optional GPS capture and historical-location fallback. Removed default/automatic GPS acquisition from HistoryTimeline; the backend timeline uses payment coordinates first and legacy coordinates only as fallback, never merchant coordinates.
- Restored the missing purchase image-draft state used by the existing photo-recognition flow.
- Verification: focused location/invoice/payment/purchase/migration tests: **71 passed, 4 warnings**; timeline preference/fallback tests: **2 passed, 4 warnings**; frontend production build: **passed**, Vite 5.4.21, 45 modules.

## Phase 4 — Sprint 5 transaction/evidence workflows

- Added authenticated `GET /invoices/{invoice_id}/original` and `GET /payments/purchases/{purchase_id}/photo`. Both authorize by scoped record lookup before resolving the stored key; inaccessible, keyless, and missing-object records return 404. Invoice MIME is inferred from its generated filename extension; purchase MIME comes from the stored record. No client-supplied storage key is accepted.
- Purchase deletion now returns a storage failure instead of deleting the database row when photo cleanup fails, preserving the only evidence reference. Existing transaction-attachment owner scoping is unchanged.
- Added regressions for invoice original bytes/MIME, cross-user denial and missing objects; purchase photo bytes/MIME, foreign-user/no-photo/missing-object 404s; and purchase record preservation on storage-delete failure.
- Verification: affected API/evidence/location/org/user-isolation files with `OPEN_REGISTRATION=1`: **114 passed, 4 warnings**. Focused attachment owner retrieval: **1 passed, 4 warnings**.

## Phase 5 — Integration and acceptance

- Full backend suite: **341 passed, 11 failed, 42 warnings**. All 11 failures match the known environment blockers: nine Gmail lifecycle async tests cannot run because no pytest async plugin is active; two real purchase-memory model tests receive no embedding because ONNX Runtime is unavailable. No tests were skipped or weakened.
- Frontend production build: **passed**, Vite 5.4.21, 45 modules. This checks compilation/bundling, not interactive UI workflows (no frontend automated test harness is declared).
- `git diff --check`: **passed**. Existing unrelated modified/untracked user files were left in place. No dependencies, remote services, or artifacts were installed/downloaded; no commit, push, or deployment was performed.
- Residual limitations: invoice original content type uses extension-based MIME inference because invoice rows do not persist validated MIME; the endpoint safely falls back to `application/octet-stream`. Organization-scoped invoice retrieval follows existing active-organization lookup behavior. Purchase list still exposes stored file metadata/key as before; retrieval itself is ID-based and does not trust that client value.

## Phase 7 — Physical/online location and two-account verification (2026-11-08)

- Updated `frontend/src/Purchases.jsx` with a physical/online/remote context selector. Physical purchases can optionally include a manually entered merchant latitude/longitude pair; a complete pair is sent only as `merchant_*` and marked user-confirmed. Device GPS remains an explicit opt-in and is sent only as `payment_*`; online/remote contexts do not submit merchant coordinates from device GPS.
- Extended workflow regressions: physical merchant coordinates are different from payment GPS and both persist independently through the purchase list; online payment GPS persists while merchant coordinates remain null in create and list responses. Added a two-token isolation scenario: owner A can read invoice/original, purchase photo and transaction list entry; user B gets 404 for invoice/original/photo and cannot see A's invoice, purchase, or transaction in lists. There is no transaction-detail GET route, so transaction isolation is verified at the supported list surface only.
- Targeted verification: `./.venv/bin/python -m pytest -q tests/test_web_mvp_workflows.py::test_workflow_3_online_expense_with_evidence tests/test_web_mvp_workflows.py::test_workflow_4_physical_expense_location_and_invoice tests/test_user_isolation.py::test_two_accounts_cannot_access_invoice_image_purchase_photo_or_transactions --tb=short`: **3 passed, 4 warnings** (FastAPI `on_event` deprecations).
- Frontend build: `npm --prefix frontend run build`: **passed**, Vite 5.4.21, 45 modules. `git diff --check`: **passed**. An initial `npm run build` from repository root failed because `package.json` is in `frontend/`; rerun from the correct package path passed.
- Browser acceptance was attempted with local API/frontend servers and the ZCode in-app browser. The API failed once when a first isolated database path did not match this app's database configuration; restarting with `DATABASE_PATH` on a fresh `/tmp` database succeeded. The browser runtime repeatedly returned `browser guest not attached (webview not ready)` when opening the local frontend. A retry on 2026-11-08 found no controlled or user-open tabs, and creating a fresh tab failed with the same attach error. No GUI interaction, map-marker visual confirmation, screenshot, or browser DOM validation was completed. Do not interpret API round-trip or build as proof that the marker rendered.
- No commits, pushes, deployment, dependency installation, remote changes, model downloads/replacements, or destructive Git operations were performed. Existing unrelated dirty worktree files were preserved.


- Matching reload loop: added `unique`/`ambiguous`/`none` candidate readback (with `payment_verified: false`), link→refetch and unlink→refetch persistence, and partial-link transitions in `tests/test_payments_api.py`; added unlink control in `Ledger.jsx` and candidate allocation display + manual-invoice picker in `Invoices.jsx`.
- Cross-workflow acceptance: new `tests/test_web_mvp_workflows.py` (5 passed) covers upload→edit→save→reopen, invoice/QR→payment→unique match, online purchase→evidence→transaction, physical purchase location/context round-trip, and failed-upload→manual-entry recovery.
- Purchase list now returns only `photo_available` (no `source_file`/`file_checksum`/`file_size`/`mime_type`); `test_purchase_api.py` asserts the absence and reads storage keys from the repository.
- Invoice MIME: added `Invoice.mime_type`, persisted `validated.mime_type` at `/upload`, idempotent `_ensure_invoice_mime_column` migration (with test), and persisted-MIME serving in `GET /invoices/{id}/original` with extension fallback.
- Map policy: `HistoryTimeline` keeps payment-first/legacy-fallback; `Purchases` plots merchant coordinates only; zero-coordinate checks fixed where touched.
- Verification: focused suite **100 passed**; full suite **349 passed, 11 failed** (unchanged 9 Gmail async + 2 ONNX blockers); frontend build **passed** (Vite 5.4.21, 45 modules); `git diff --check` **passed**. No commits, pushes, deployment, dependency installs, or destructive Git operations.
