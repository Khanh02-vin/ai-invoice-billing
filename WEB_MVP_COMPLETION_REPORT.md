# Web MVP Completion Report

**Branch:** `exp1`  
**Completion date:** 2026-11-08  
**Status:** Implemented and verified with known environment blockers  
**Execution record:** [WEB_MVP_EXECUTION_LOG.md](WEB_MVP_EXECUTION_LOG.md)

## Delivered

### ADR-0002 location behavior

- Invoice updates now validate and persist legacy location and the distinct payment-time/device, merchant, and purchase-context fields, including evidence references and explicit user confirmation.
- Coordinate pairs/ranges and related confidence/accuracy/context values are validated. Zero latitude/longitude values are retained as real coordinates.
- Purchases and Ledger present device/payment-time location separately from merchant location. GPS remains optional and explicitly user-triggered; the UI clarifies GPS is not proof of a merchant location or payment.
- HistoryTimeline no longer requests GPS automatically or substitutes a default city location. Timeline points use payment coordinates first, legacy location as fallback, and never merchant coordinates.
- Restored the existing purchase photo draft-file state.

### Evidence retrieval and lifecycle

- `GET /invoices/{invoice_id}/original` serves the original file only after user/active-organization-scoped invoice lookup.
- `GET /payments/purchases/{purchase_id}/photo` serves an image only after owner-scoped purchase lookup.
- Both endpoints derive storage keys from authorized database records, not client parameters, and return 404 for unauthorized/missing records or absent content.
- Purchase deletion preserves its row/reference if storage deletion raises. Transaction attachment access remains owner-scoped.

## Verification

- Focused location/invoice/payment/purchase/migration suite: **71 passed, 4 warnings**.
- Timeline coordinate preference/fallback cases: **2 passed, 4 warnings**.
- Affected API/evidence/location/organization/user-isolation suite with `OPEN_REGISTRATION=1`: **114 passed, 4 warnings**.
- Focused transaction attachment ownership test: **1 passed, 4 warnings**.
- Frontend production build `(cd frontend && npm run build)`: **passed**, Vite 5.4.21, 45 modules.
- `git diff --check`: **passed**.
- Full backend suite: **341 passed, 11 failed, 42 warnings**. Failures: nine Gmail async tests require a pytest async plugin absent from this environment; two purchase-memory real-model tests could not produce embeddings because ONNX Runtime is unavailable. These are the same environment blockers observed at baseline; no tests were skipped or weakened.

The frontend build is a bundle/compile check, not an interactive UI acceptance test; the project does not declare an automated frontend test harness.

## Residual limitations

- ~~Invoice rows do not persist the validated original MIME type.~~ Resolved below (2026-11-08 re-verification): `Invoice.mime_type` is persisted from validated upload MIME, with extension-based inference kept only as fallback in `GET /invoices/{id}/original`.
- ~~The purchase list continues to return its existing storage metadata/key fields.~~ Resolved below: the list now returns only `photo_available`, and the ID-based owner-scoped `/photo` endpoint remains the sole content retrieval path.
- Existing Gmail async/plugin and ONNX Runtime-dependent tests remain unverified in this environment.

## Re-verification addendum (2026-11-08)

### P0 — Transaction matching UI loop
- Added API reload/readback regressions covering `unique`, `ambiguous`, and `none` candidate states (with `payment_verified: false`), link-then-refetch persistence, unlink-then-refetch restoration, and partial-link transitions — all through fresh `TestClient` calls.
- `Ledger.jsx` now exposes an **unlink** control for the matched invoice and refreshes state; `Invoices.jsx` shows allocated/remaining amounts and provides a manual invoice picker when no candidate is found.

### P0 — Five cross-workflow acceptance tests
New `tests/test_web_mvp_workflows.py` covers through the real API: (1) upload → edit → save → reopen, (2) QR/invoice data → payment transaction → unique matching, (3) online purchase → evidence attachment → transaction, (4) physical purchase with merchant location/context round-trip, (5) failed upload → manual entry recovery (OCR via real sync extractor for text; current seams mocked where images are involved).

### P1 — Storage metadata / invoice MIME
- `GET /payments/purchases` no longer returns `source_file`, `file_checksum`, `file_size`, or `mime_type`; it returns `photo_available`. Frontend consumers did not depend on the removed fields.
- `Invoice.mime_type` persists `validated.mime_type` from `/upload`, plus an idempotent `_ensure_invoice_mime_column` migration (with regression test); `GET /invoices/{id}/original` serves the persisted value.

### Map policy separation
- `HistoryTimeline` keeps the payment-first/legacy-fallback movement map (unchanged).
- `Purchases` now plots **merchant coordinates only**; payment/device GPS and legacy coordinates are no longer promoted to merchant position. Zero-coordinate visibility is fixed (`??` / `!= null`) where touched.

### Verification results
- Focused suite (payments, purchase, workflow, migration, upload validation, API, review): **100 passed, 4 warnings**.
- Full backend suite: **349 passed, 11 failed, 42 warnings** — the 11 failures are unchanged environment blockers (9 Gmail async plugin, 2 ONNX Runtime).
- Frontend production build `(cd frontend && npm run build)`: **passed**, Vite 5.4.21, 45 modules.
- `git diff --check`: **passed**.

## Re-verification addendum — Physical/online locations and user isolation (2026-11-08)

- Purchases now lets the user select physical, online, or remote context. For physical purchases, the user may enter a merchant latitude/longitude pair separately; only a complete pair is sent as `merchant_*`. The existing opt-in device GPS action remains `payment_*` and is never copied into merchant fields. Online and remote purchases do not submit merchant coordinates from device GPS. The map's existing merchant-only filtering remains unchanged.
- Added/extended API workflow regressions proving physical merchant coordinates and payment GPS round-trip separately, and online GPS does not populate merchant location. Added a two-account test proving account B cannot read account A's invoice metadata/original or purchase photo and cannot see A's invoice, purchase, or transaction in supported lists. There is no transaction-detail GET endpoint; only the existing transaction-list isolation surface is claimed.
- Targeted result: **3 passed, 4 warnings**. Warnings are FastAPI lifecycle `on_event` deprecations.
- Frontend build: **passed**, Vite 5.4.21, 45 modules. `git diff --check`: **passed**.
- **Browser/map visual acceptance remains unverified.** A local isolated API and frontend were started for a UI attempt, but the in-app browser returned `browser guest not attached (webview not ready)` on both tries. No page DOM, screenshot, form interaction, or rendered marker was observed; API persistence tests do not establish visual map rendering. A further retry on 2026-11-08 found no controlled or user-open browser tabs; creating a fresh tab failed with the same attach error, so visual acceptance remains blocked.


All changes were made in the existing worktree. No commit, push, deployment, dependency installation, remote-service change, model replacement/download, or destructive Git operation was performed. Pre-existing unrelated dirty files were preserved.
