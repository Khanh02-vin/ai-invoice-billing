# Sprint 2 — Upload Validation Normalization Report

**Status: PARTIALLY_VERIFIED**

## 1. Objective and scope

Normalize invoice readability/validation for `POST /upload` and `POST /payments/camera-upload` while preserving endpoint-specific ingestion and processing behavior. Specifically, a missing invoice number must not invalidate an otherwise usable retail receipt, but documents with no usable financial amount must still be rejected.

This change does not rewrite OCR or alter the LLM fallback or payment matching policy. No new database schema was introduced for this Sprint 2 change. No commit, push, or deployment was performed.

## 2. Root cause

The endpoints had separate acceptance gates. The `/upload` worker required both an invoice number and a positive `total`, so an otherwise readable Vietnamese retail receipt without a formal invoice number was marked failed. Camera upload followed a different extraction/persistence path and did not share the same testable readability rule. A single receipt could therefore be treated differently depending on its upload route.

The persistence layer also exposed two relevant behaviors during endpoint regression testing: duplicate invoice numbers were raised as a generic exception, and same-checksum upserts attempted to reuse an existing row's primary key on a separate ORM instance. Sprint 2 endpoint tests exercised these cases, so repository duplicate handling now preserves typed number-duplicate information and returns the existing row for same-file deduplication.

## 3. Before/after validation rules

| Case | Before | After |
|---|---|---|
| Missing invoice number, financial amount present | `/upload` rejected; camera path differed | Accepted by both; number remains `unknown` if not extracted |
| No positive total, subtotal, amount due, or positive line-item amount | Endpoint-dependent | Rejected by both as unreadable |
| Invoice number present and financial amount present | Accepted | Accepted; extracted fields and provenance retained |
| Same file uploaded again | Repository could attach a second ORM object with same ID | Existing persisted invoice is returned |
| Same non-empty invoice number, different file | Generic duplicate exception behavior | Typed duplicate error; `/upload` preserves a failed attempt placeholder, camera responds 409 and removes the rejected file |

`invoice.id` remains the database identity. `invoice_number` remains optional metadata and an application-level duplicate signal only; blank and `unknown` values do not participate in number-based duplicate detection. The readability gate accepts at least one positive amount from `total`, `subtotal`, `amount_due`, or a line item amount; it does not merely accept any non-empty line-item collection.

## 4. Production call chains

### `POST /upload`

`src/app.py:488` validates and stores the upload, creates a processing placeholder, then runs the synchronous worker when background processing is disabled (as in the endpoint tests) or queues Celery otherwise. The synchronous path enters `src/workers/extraction_worker.py:45` (`_do_extract`), calls the existing production extractor, applies the shared gate in `src/domain/invoice_rules.py:48`, and updates the placeholder through `InvoiceRepository.upsert`. The route then returns the current upload result from the repository (`src/app.py:616-620`).

If extraction fails synchronously, the placeholder remains pollable with `FAILED` status. On invoice-number duplication, the rejected attempt number is reset to `unknown`, while extracted financial fields and provenance remain on that failed attempt row; the existing invoice is not overwritten. Celery retry behavior remains distinct: the worker wrapper still handles retries and sets the final failure status only after retries are exhausted.

### `POST /payments/camera-upload`

`src/routers/payments.py:872` validates and stores the image, invokes the production `extract_invoice` path, applies `is_unreadable_invoice`, and synchronously upserts the result. It preserves its camera-specific synchronous response and `needs_review` calculation. On duplicate invoice number it returns typed 409 `DUPLICATE_INVOICE` and deletes the second stored file. Extraction/app errors also clean up the uploaded file. Camera regression tests patch the OCR boundary to supply controlled fixture text while retaining the production extractor entrypoint, readability gate, and repository persistence path.

### Persistence and identity

`src/store/sql_repository.py:333` normalizes nested JSON values before writes, returns the stored row for same-file checksum duplicates, and raises `DuplicateInvoiceError` for a conflicting non-empty invoice number. `_row_to_invoice` deserializes JSON while retaining mapping-shaped provenance, matching existing API/review consumers. `src/models.py:123` now declares provenance as `dict[str, Any]`, consistent with the JSON dictionary representation; extracted and corrected provenance contents are retained.

## 5. Changed files and locations

- `src/domain/invoice_rules.py:30-55` — shared positive-financial-amount predicate and unreadable-document rule. `UNEXTRACTED` documents `"unknown"` and empty marker values at line 27.
- `src/workers/extraction_worker.py:124-128` — shared readability gate for `/upload`; duplicate failure handling retains the failed attempt without persisting the conflicting invoice number (`:214-223`).
- `src/routers/payments.py:909-943` — camera review flag, shared readability gate, typed duplicate mapping and uploaded-file cleanup.
- `src/store/sql_repository.py:282-315,333-382,415-487` — provenance JSON representation, serialization at write boundary, safe same-checksum return, typed number duplicate, and correction metadata handling. Note: the file contains separate pre-existing location/sync changes not attributable to Sprint 2; these were already present in the worktree and are excluded from this report's Sprint 2 scope.
- `src/models.py:123` — provenance annotation aligned with JSON dictionary payload. The file also contains separate pre-existing location-field changes not attributable to Sprint 2.
- `tests/test_upload_validation.py:111-455` — endpoint regressions for no-number receipts, formal invoices, missing amounts/junk, uncertain extraction, same-file deduplication, conflicting numbers, and unsupported types on both endpoints.

## 6. Regression tests and results

### Passed

- Focused Sprint 2/extraction/payment/review set:
  `./.venv/bin/python -m pytest tests/test_upload_validation.py tests/test_vi_extractor.py tests/test_vn_receipts.py tests/test_line_items.py tests/test_xml_matching.py tests/test_qr.py tests/test_payments_api.py tests/test_review.py -q --tb=short`
  **95 passed, 8 warnings**.
- Upload/review/API/payment compatibility set:
  `./.venv/bin/python -m pytest tests/test_upload_validation.py tests/test_review.py tests/test_api.py tests/test_payments_api.py -q --tb=short`
  **59 passed, 4 warnings**.
- The endpoint-specific `tests/test_upload_validation.py` contains 16 regressions covering both routes and their endpoint-specific failure responses.

### Full backend suite

Command: `./.venv/bin/python -m pytest -q --tb=no`

Result: **315 passed, 11 failed, 42 warnings**. No Sprint 2 or review tests failed in this run. The remaining failures are:

- 9 tests in `tests/test_gmail_lifecycle.py`: this environment does not load an async pytest plugin, so async tests report “async def functions are not natively supported.” Although `pyproject.toml` declares `pytest-asyncio` and `asyncio_mode = "auto"`, the active `.venv` pytest run reports `PytestUnknownMarkWarning` for `pytest.mark.asyncio`, indicating the plugin is not installed/active in this environment.
- 2 tests in `tests/test_purchase_memory.py`: the optional ONNX model needed to produce embeddings is absent, causing `embed()` to return `None`.

These failures are outside the Sprint 2 upload validation paths, but until the full suite runs with the missing test plugin and optional model available, full-suite verification is incomplete.

## 7. Remaining risks and constraints

- The full backend suite is not fully green because of the environment prerequisites above; do not treat 315 passing tests as a clean full-suite result.
- Pydantic may emit serializer warnings in unrelated existing paths where a model instance contains raw dictionary JSON values. The declared provenance annotation now matches that dictionary payload and focused tests pass, but these warnings are not suppressed or hidden by this report.
- This work preserves existing file validation, required financial readability, duplicate policy, extraction results and provenance. OCR, LLM fallback, payment matching, and database schema were not intentionally changed for Sprint 2.
- Worktree contains unrelated in-progress changes (including location/sync migration edits); review the full diff before staging or committing so they are not accidentally included.

## 8. Verification status

**PARTIALLY_VERIFIED** — shared validation and both endpoint paths are covered by passing focused regressions, including existing review and payment API tests. Full-backend verification is blocked only by the uninstalled/inactive async pytest plugin and absent optional ONNX embedding model; this report records that result without labelling the full suite green.

## 9. Operational boundary

No commit, push, release, or deployment was performed. The existing working tree and its unrelated user changes were left in place.
