# SPRINT 1 — LLM Fallback Production Report

## Status: VERIFIED

## 1. Root cause

- File: `src/extract/extractor.py`
- Production entrypoint `extract_invoice(content, mime_type, source_file="unknown", user_id="")` (line 722) called `extract_from_text(text, source_file=source_file, layout=layout)` **without** the `llm=` argument, so `llm` defaulted to `None` (live definition, line 1462).
- The LLM fallback branch `if llm is not None and confidence < _llm_threshold():` (line 1473) was therefore unreachable from every real path: Celery worker (`_do_extract` → `extract_fn`), async upload, `camera_upload`, IMAP poller, and batch extractor.
- Existing tests masked the bug because they inject `llm=` directly into `extract_from_text` (e.g. `tests/test_llm_fallback.py`).
- Verified empirically: an instrumented spy wrapping `extract_from_text` recorded `llm=None` while `extract_invoice(odd_invoice_text, "text/plain")` ran → confidence 0.6, total 0.0, LLM never invoked.

Provider discovery: `get_llm_provider()` (`src/llm/base.py:96`) returns `OpenAIProvider()` when `LLM_API_KEY`/`OPENAI_API_KEY` is set, else `None` — so the fix is safe when no key is configured (regex-only behavior preserved).

## 2. Production call chain

### Before

```
upload endpoint (src/app.py) / camera_upload (src/routers/payments.py:897)
  → celery task extract_invoice (src/workers/extraction_worker.py:240)
    → _do_extract
      → extract_fn = src.extract.extractor.extract_invoice
        → _extract_text_with_layout(content, mime_type)
        → extract_from_text(text, source_file=..., layout=layout)   # llm=None
          → _extract_regex(...) → confidence
          → if llm is not None and confidence < threshold:  # DEAD CODE
        → _qr_decode(...)
```

### After

```
upload endpoint / camera_upload
  → celery task extract_invoice
    → _do_extract
      → extract_fn = src.extract.extractor.extract_invoice
        → _extract_text_with_layout(content, mime_type)
        → extract_from_text(text, source_file=..., llm=get_llm_provider(), layout=layout)
          → _extract_regex(...) → confidence
          → if llm is not None and confidence < threshold:  # LIVE
              _extract_llm(...) → _grounded(...) → _merge_fields(...) → confidence=1.0
        → _qr_decode(...)
```

One injection point (`extract_invoice`) covers worker, camera upload, IMAP poller and batch extractor — they all funnel through it.

## 3. Changed files and exact code locations

### `src/extract/extractor.py`
- Line 21 — added import: `from ..llm.base import get_llm_provider`
- Lines 722–745 — `extract_invoice`: now resolves the provider at the production boundary:
  ```python
  invoice = extract_from_text(
      text, source_file=source_file, llm=get_llm_provider(), layout=layout
  )
  ```
  Signature unchanged; no new public `llm` parameter (YAGNI). No OCR/invoice-validation/payment-matching logic touched.

### `src/llm/base.py`
- Line 43 — `self._client = None` in `OpenAIProvider.__init__`.
- Lines 46–52 — new `client` property lazily creating and caching the `OpenAI` instance.
- Lines 54–75 — `complete` and `complete_image` now use `self.client` instead of constructing `OpenAI(...)` per call (avoids a new HTTP pool per request).

## 4. Regression tests added

New file: `tests/test_llm_fallback_production.py` (10 tests), all exercising the actual production entrypoint `extract_invoice` (not only the internal helper):

1. `test_production_entrypoint_passes_llm` — monkeypatches `src.extract.extractor.get_llm_provider` with a counting provider; asserts the provider is called exactly once and fields are filled from the LLM. Fails without the fix.
2. `test_production_fallback_skipped_when_regex_sufficient` — `ExplodingProvider` (raises if called); asserts no LLM call on a high-confidence invoice.
3. `test_production_llm_failure_does_not_crash` — garbage LLM response; no crash, regex result retained.
4. `test_production_llm_disabled_keeps_legacy_behavior` — provider `None`; result equals `extract_from_text` without LLM.
5. `test_production_valid_invoice_unchanged` — sample invoice values unchanged, user_id/id set.
6. `test_production_sourcefile_and_id_set_for_worker` — source_file propagated, id not derived from invoice_number.
7. `test_worker_task_shares_production_entrypoint` — `src.workers.extraction_worker.extract_fn is src.extract.extractor.extract_invoice`.
8. `test_llm_call_threshold_still_gates_production` — `LLM_CALL_THRESHOLD=0.0` still skips LLM.
9. `test_llm_provider_resolved_from_llm_module` — `None` when no key, real provider when key present.
10. `test_openai_provider_reuses_client` — fake `openai` module; only one client instance across two calls.

## 5. Test execution results

- New regression suite: **10 passed** (`tests/test_llm_fallback_production.py`).
- Existing extraction tests: **93 passed** (`test_llm_fallback.py`, `test_invoice.py`, `test_qr.py`, `test_line_items.py`, `test_vn_receipts.py`, `test_sroie_regression.py`, `test_utils.py`, plus the new file).
- Full backend suite: **296 passed, 11 failed** (`tests/` total 307).
  - The 11 failures are pre-existing and environmental, identical to the baseline recorded before the change:
    - `tests/test_gmail_lifecycle.py` × 9 — `async def functions are not natively supported` (pytest-asyncio declared in `pyproject.toml` but not installed in `.venv`; `pytest-asyncio` absent).
    - `tests/test_purchase_memory.py` × 2 — `assert emb is not None and len(emb) == 4096` fails with `None` (requires a real embedding model not present).
  - No extraction, invoice, payment-API, or worker test regressed.
- Verification that the test catches the original bug: temporarily reverted `extract_invoice` to `llm` omitted → `test_production_entrypoint_passes_llm` failed with `provider.calls == 0`; restored the fix → pass.

## 6. Remaining risks

- The dead duplicate `extract_from_text` definition at `src/extract/extractor.py:566` (older `(text, source_file="")` signature) still exists and shadows nothing (live definition is line 1462); left untouched to keep the diff minimal/reviewable. Could be removed later.
- `ai-engine/src/invoice_ai/extract/extractor.py:313` (legacy copy) also omits `llm=`; it is not wired into `src/` or `main.py`, so out of scope.
- `get_llm_provider()` constructs a new `OpenAIProvider` (3 env reads) per `extract_invoice` call; only the HTTP client is cached per instance. If desired, a module-level singleton provider could be added later, but the current change satisfies "no new HTTP client per request".
- LLM_MODE, threshold, grounding and merge semantics are unchanged; `fill` mode means regex keeps contested fields (e.g. vendor "HÓA ĐƠN THƯƠNG MẠI" on `ODD_INVOICE`).
- Gmail/purchase-memory test failures are environmental (missing pytest-asyncio plugin and embedding model) and unrelated to this sprint.

## 7. Conclusion

Production entrypoint now resolves and passes the LLM provider through the existing architecture; regression tests exercise the real entrypoint and fail without the fix. Status: **VERIFIED**.
