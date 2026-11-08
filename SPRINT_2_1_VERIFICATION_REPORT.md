# Sprint 2.1 — Verification Closure Report

**Status: PARTIALLY VERIFIED**  
**Verification date:** 2026-11-07  
**Branch:** `exp1`

## 1. Objective and operational boundary

Sprint 2.1 independently verified the backend test failures recorded by Sprint 2, audited invoice duplicate semantics in both repository implementations, checked legacy JSON compatibility in review updates, and tested camera-upload evidence-file cleanup. Changes were limited to regressions supported by the repository’s existing multi-tenant policy and observed failures.

No dependencies were installed, no model was downloaded or replaced, and no commit, push, deployment, remote configuration change, or Sprint 3 work was performed. Existing user changes were preserved.

## 2. Baseline and attribution

The worktree was already dirty at the start of this verification. The initial status contained modified manifests, application/repository/extractor/model/worker files, and tests, plus untracked Sprint 1/Sprint 2 reports, docs, migration/domain files, and tests. Therefore, the complete diff against `HEAD` is **not** a Sprint 2.1-only diff. The changed source and test files below were already modified before this verification; only the targeted regression/fix hunks are attributed to Sprint 2.1. No unrelated worktree content was reset or overwritten.

`git diff --check` passed before report creation. The full test output was captured at `/tmp/sprint-2-1-full-pytest.txt`.

## 3. Historical failures: fresh reproduction

The historical outcome was reported as 315 passed and 11 failed. A fresh full run reproduced the same 11 failure count and failure groups, with additional tests passing in the current worktree:

| Run | Result | Finding |
|---|---:|---|
| Full backend suite: `./.venv/bin/python -m pytest -q --tb=short` | **323 passed, 11 failed, 42 warnings** | 334 tests; no skips. Nine Gmail lifecycle failures and two purchase-memory failures, listed below. |
| `tests/test_gmail_lifecycle.py` | **9 failed** | Every async test reports `async def functions are not natively supported`; pytest also emits `PytestUnknownMarkWarning` for `pytest.mark.asyncio`. |
| `tests/test_purchase_memory.py` | **10 passed, 2 failed** | Both failures require an embedding and receive `None`. |

### Gmail test harness

The active `.venv` does not register `pytest-asyncio`. The test-collection/plugin trace showed no pytest-asyncio plugin, and the async marker is unknown. The repository’s `pytest.ini` contains only `pythonpath = ai-engine/src`, overriding pytest settings in `pyproject.toml` (including `asyncio_mode = "auto"`). `pyproject.toml` declares `pytest-asyncio>=0.23.0`, but the currently active environment does not load it. No package was installed to force these tests green.

Failing IDs:

- `tests/test_gmail_lifecycle.py::test_gmail_oauth_start_and_state_is_single_use`
- `tests/test_gmail_lifecycle.py::test_gmail_oauth_callback_stores_encrypted_refresh_and_starts_watch`
- `tests/test_gmail_lifecycle.py::test_worker_access_token_and_watch_send_expected_google_requests`
- `tests/test_gmail_lifecycle.py::test_worker_invalid_grant_raises_dedicated_error`
- `tests/test_gmail_lifecycle.py::test_worker_start_watch_clears_invalid_grant_and_persists_watch`
- `tests/test_gmail_lifecycle.py::test_worker_renewal_and_stop_revoke`
- `tests/test_gmail_lifecycle.py::test_worker_sync_fetches_metadata_only_deduplicates_and_renews`
- `tests/test_gmail_lifecycle.py::test_pubsub_authenticates_verification_token_and_dispatches`
- `tests/test_gmail_lifecycle.py::test_pubsub_rejects_missing_auth_and_bad_payload`

### Purchase-memory model

The configured model exists at `models/mobilenetv3_embed.onnx` and is 6,074,934 bytes. The active Python 3.14.4 environment reports `importlib.util.find_spec("onnxruntime") is None`. Thus, the prior Sprint 2 report’s attribution that the model file was absent is stale: current evidence shows the missing prerequisite is ONNX Runtime, not the model file. No model or dependency was downloaded or replaced.

Failing IDs:

- `tests/test_purchase_memory.py::test_real_embed_unit_vector_and_rejects_bad_content`
- `tests/test_purchase_memory.py::test_real_model_matches_rephoto_without_hash_or_text`

## 4. Audit findings and evidence-backed fixes

### Duplicate behavior: active SQLModel repository

The multi-tenant policy in `docs/multi-tenant-spec.md` defines organization invoices as shared across members and keeps personal writes in a separate `organization_id IS NULL` namespace. The SQLModel repository now scopes duplicate checks accordingly: organization records by `organization_id`; personal records by both `user_id` and null organization. Checksum deduplication remains first, and only a non-empty existing checksum counts as a same-file match. This prevents empty checksums from accidentally turning an invoice-number conflict into a same-file merge.

The invoice-number edit path now checks for a conflicting meaningful number in the same personal or organization context and raises `DuplicateInvoiceError`. `PUT /invoices/{invoice_id}` maps that typed exception to HTTP 409 `DUPLICATE_INVOICE` with the existing invoice ID, rather than allowing the edit or returning an untyped server error.

### Duplicate behavior: legacy raw-SQL repository

The legacy repository’s duplicate lookup previously scoped organization rows by both member and organization, and user-only unique indexes conflated personal and organization namespaces. Lookup now shares org duplicates across members and retains per-user personal scope. Its duplicate indexes use a context expression (`personal:<user_id>` versus `org:<organization_id>`), with existing behavior retained for legacy databases where duplicate rows prevent index creation.

### Provenance and review-history compatibility

A confirmed review-update crash occurred when a valid JSON object had been stored in `review_history`: repository update appended a list to the raw object. The SQLModel update path now normalizes the fetched invoice before applying correction history. Regression coverage confirms that SQL NULL and valid JSON values with incompatible shapes do not crash review updates or reads. Mapping-shaped provenance remains available; invalid shapes normalize to the repository’s empty mapping/list defaults.

This does **not** establish safe handling for arbitrary malformed JSON text at the storage-decoder layer. That case was not included in the final regression set and is listed as a residual compatibility gap, not claimed as verified.

### Camera-upload evidence files

A same-checksum retry returns the existing invoice. The camera route now deletes the newly stored retry file when the existing invoice still points to an available canonical evidence file. Existing cleanup for unreadable documents, invoice-number conflicts, and exceptions remains in place. Camera regressions verify the existing evidence file remains, the duplicate retry file is removed, and a conflicting-number upload’s new file is deleted.

The fallback branch that retains the retry file and updates `source_file` when the old evidence file has disappeared is present, but was not independently covered by a dedicated regression. It is reported as residual risk below.

## 5. Verification results

| Scope | Result |
|---|---:|
| Legacy raw-SQL duplicate/batch/line-item set: `tests/test_dedup.py tests/test_batch.py tests/test_line_items.py` | **19 passed, 7 warnings** |
| Extraction/payment/review compatibility set: `tests/test_upload_validation.py tests/test_vi_extractor.py tests/test_vn_receipts.py tests/test_line_items.py tests/test_xml_matching.py tests/test_qr.py tests/test_payments_api.py tests/test_review.py` | **97 passed, 8 warnings** |
| Broader upload/review/API/payment/invoice/organization focused set (run earlier in this verification) | **96 passed, 4 warnings** |
| Focused organization/review/camera regression set (run earlier in this verification) | **25 passed, 4 warnings** |
| `git diff --check` | **Passed; no output** |
| Full backend suite | **323 passed, 11 failed, 42 warnings**; failures limited to the nine async-harness cases and two model-runtime cases above |

The focused tests verify the added organization duplicate sharing and personal/org separation behavior, same-file deduplication, edit conflict response, historical JSON shape normalization, and camera evidence cleanup. The full suite remains partially verified due to the environment prerequisites; it is not labelled green.

## 6. Residual risks and unresolved policy

- **Concurrent SQLModel duplicate inserts:** the active SQLModel repository uses application-level duplicate pre-checks but has no database unique constraints for invoice checksum/number. Two concurrent inserts can still race. No constraint/migration was added because this verification did not establish a safe migration for pre-existing duplicates.
- **Legacy invoice-number edits:** the legacy raw-SQL repository’s edit-to-duplicate path was not covered by the new regression set. The active application repository uses SQLModel; legacy update behavior remains an audit item rather than a claimed fix.
- **Evidence file recovery:** the branch for a missing original camera evidence file was not directly regression-tested.
- **Malformed raw JSON text:** incompatible valid JSON shapes and SQL NULL are covered; malformed text that fails in the SQLAlchemy JSON decoder is not.
- **Duplicate identity policy:** whether seller/MST should distinguish otherwise identical invoice numbers is not defined by the existing policy and was not changed.
- **Environment:** Gmail async tests need a correctly active async pytest plugin; purchase-memory model tests need ONNX Runtime installed in the active environment. Neither prerequisite was altered as part of verification.
- **Existing Pydantic/deprecation warnings:** warnings remain visible in passing suites and were not suppressed or weakened.

## 7. Changed paths and scope notes

Sprint 2.1 regression/fix hunks are in the already-dirty paths `src/app.py`, `src/routers/payments.py`, `src/store/sql_repository.py`, `src/store/repository.py`, `tests/test_dedup.py`, `tests/test_organizations.py`, and `tests/test_review.py`, plus camera assertions in `tests/test_upload_validation.py`. These files also contain earlier user work; their entire current diffs must not be attributed to Sprint 2.1. The other initial modified/untracked paths were preserved.

## 8. Final status

**PARTIALLY VERIFIED.** The targeted duplicate, compatibility, and camera suites pass. The full backend suite has 323 passes and 11 reproducible environment-related failures (nine async pytest harness failures and two missing-ONNX-Runtime embedding failures). This report records rather than masks those failures.

No commit, push, deployment, remote change, dependency installation, model download/replacement, or Sprint 3 work was performed. Verification stops here.
