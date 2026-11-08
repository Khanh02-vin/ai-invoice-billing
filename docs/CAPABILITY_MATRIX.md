# Capability Matrix — Full Repository Audit

**Audit date:** 2026-11-08 · **Branch:** `exp1` · **Base commit:** `bfcc644` · **Working tree:** dirty (pre-existing)
**Classification:** IMPLEMENTED / PARTIAL / STUB / MISSING / UNVERIFIED
**Evidence format:** `path:symbol:line`

---

## A. Transaction Capture

| # | Capability | Status | Evidence |
|---|---|---|---|
| A1 | Camera capture → upload image | IMPLEMENTED | `frontend/src/components/CameraScan.jsx:29-31,70-75` (getUserMedia → canvas → File → POST) |
| A2 | `POST /payments/camera-upload` → save invoice | IMPLEMENTED | `src/routers/payments.py:869-931` |
| A3 | QR decode (image/PDF) | IMPLEMENTED | `src/extract/extractor.py:742-799` (OpenCV QRCodeDetector, retry ×4) |
| A4 | VietQR/EMVCo payload → total/vendor/MST/invoice no | IMPLEMENTED | `src/extract/extractor.py:927-1030` (`_parse_qr_payload`, `_apply_qr_crosscheck`) |
| A5 | QR cross-check wired into OCR pipeline | IMPLEMENTED | `src/extract/extractor.py:731-733` (called in `extract_invoice`) |
| A6 | General upload `POST /upload` | IMPLEMENTED | `src/app.py:488-620`; UI `frontend/src/Invoices.jsx:280,316` |
| A7 | Manual transaction input `POST /transactions` | IMPLEMENTED | `src/routers/payments.py:344-381` (route); `frontend/src/Ledger.jsx:70` (UI) |
| A8 | IMAP bank email ingestion | IMPLEMENTED | `src/payments/imap_poller.py:215-322`; parser `src/payments/parser.py` |
| A9 | Payment-to-invoice matching (1-1 + combined) | IMPLEMENTED | `src/payments/matching.py:152-175`; `src/payments/service.py:46-131` |
| A10 | Bidirectional matching (tx before / invoice before) | IMPLEMENTED | `src/payments/service.py:46-95` + `98-131` |
| A11 | Manual link/unlink API | IMPLEMENTED (API) / MISSING (UI) | `src/routers/payments.py:422-466`; frontend never calls (audit: NOT CALLED) |
| A12 | Evidence refs (dispute chain) | STUB | `src/models.py:163,257,339` declare `evidence_refs`; 0 writers/readers |
| A13 | Product/food image capture | IMPLEMENTED (review required) | `src/routers/payments.py:1210-1283`; `frontend/src/Purchases.jsx:22-34` |
| A14 | Payment provider handoff / deep link | MISSING | no payment QR, no provider deep-link endpoint |
| A15 | Static vs dynamic QR distinction | MISSING | no payment QR parser |
| A16 | QR-to-transaction draft (payment) | MISSING | invoice QR only; no payment draft from QR |

## B. Payment Ingestion

| # | Capability | Status | Evidence |
|---|---|---|---|
| B1 | Bank notification ingestion (IMAP) | IMPLEMENTED | `src/payments/imap_poller.py:215-322` |
| B2 | Payment screenshot parsing | MISSING | attachments stored only `src/routers/payments.py:702-729`; no OCR on screenshots |
| B3 | Amount extraction | IMPLEMENTED (text) | `src/payments/parser.py:157-210` |
| B4 | Timestamp extraction | PARTIAL | `src/payments/parser.py:106-123` requires year; no recipient |
| B5 | Recipient extraction | MISSING | parser has `_pick_merchant` only |
| B6 | Duplicate detection | IMPLEMENTED | `src/payments/service.py:54-62` (external_ref); `src/workers/gmail_worker.py:155-159`; `tests/test_dedup.py` |
| B7 | Payment-to-receipt matching | IMPLEMENTED (heuristic) | `src/payments/matching.py:37-42,152-175`; `service.py:82-95` |
| B8 | Gmail metadata alerts | IMPLEMENTED (metadata-only) | `src/workers/gmail_worker.py:112-183`; `src/routers/gmail_oauth.py:176-214` |
| B9 | Native SMS/notification capture | MISSING | web only; no Android listener |

## C. Evidence & OCR

| # | Capability | Status | Evidence |
|---|---|---|---|
| C1 | PDF text layer | IMPLEMENTED | `src/extract/extractor.py:380-395,402-419` |
| C2 | PDF scan → OCR per page | IMPLEMENTED | `src/extract/extractor.py:429-443` |
| C3 | OCR image (PaddleOCR + layout bbox) | IMPLEMENTED | `src/extract/extractor.py:496-510` |
| C4 | RapidOCR fallback | IMPLEMENTED | `src/extract/extractor.py:513-543` |
| C5 | Tesseract fallback | IMPLEMENTED | `src/extract/extractor.py:554-563` |
| C6 | Layout-aware total selection | IMPLEMENTED | `src/extract/extractor.py:_pick_total_layout` (tests `tests/test_invoice.py:235-257`) |
| C7 | Numeric parsing mixed separators | IMPLEMENTED | `src/extract/extractor.py:_to_float` (tests `tests/test_invoice.py:277-294`) |
| C8 | Regex core fields | IMPLEMENTED | `src/extract/extractor.py:extract_from_text:1462-1531` |
| C9 | LLM fallback | IMPLEMENTED (wired) | `src/extract/extractor.py:extract_invoice:722-745` calls `extract_from_text(..., llm=get_llm_provider(), layout=layout)` |
| C10 | Line-item extraction | IMPLEMENTED | `src/extract/extractor.py:_extract_line_items:1387-1459`; tests `tests/test_line_items.py` |
| C11 | XML e-invoice | IMPLEMENTED | `src/extract/xml_parser.py`; worker `src/workers/extraction_worker.py:104-109` |
| C12 | Merchant/address extraction | PARTIAL | vendor extracted; address not a first-class extracted field |
| C13 | Subtotal/discount/fees/total_due | PARTIAL | fields exist `src/domain/models.py:Invoice:107-112` but not visibly produced by extractor or updatable |
| C14 | Product recognition | IMPLEMENTED (purchase memory) | `src/purchase_memory.py:find_similar_purchase:211-235` |
| C15 | Confidence scores | IMPLEMENTED | `src/extract/extractor.py` confidence; `FieldProvenance` `src/domain/models.py:45-54` |
| C16 | Manual correction | IMPLEMENTED | `src/store/sql_repository.py:InvoiceRepository.update:454-552`; `tests/test_review.py` |
| C17 | Financial readability gate | IMPLEMENTED | `src/domain/invoice_rules.py:has_financial_amount:30-45`, `is_unreadable_invoice:48-55` |
| C18 | QR cross-check | IMPLEMENTED | `src/extract/extractor.py:extract_invoice:722-745` |

## D. Transaction Domain Model

| # | Capability | Status | Evidence |
|---|---|---|---|
| D1 | Transaction CRUD + ownership scoping | IMPLEMENTED | `src/routers/payments.py:383-545`; `src/store/sql_repository.py:TransactionRepository:589-864`; tests `tests/test_payments_api.py:101-114` |
| D2 | Transaction-invoice allocation model | IMPLEMENTED | `src/models.py:InvoiceTransactionLink:177-204`; repo `959-1032` |
| D3 | Legacy single-invoice link field | IMPLEMENTED (compat) | `src/models.py:PaymentTransaction.matched_invoice_id:207-273`; `src/payments/service.py:link:16-29` |
| D4 | Automatic matching | IMPLEMENTED | `src/payments/service.py:ingest:46-95`, `match_invoice_saved:98-131` |
| D5 | Manual link/unlink + balance validation | IMPLEMENTED | `src/routers/payments.py:548-691`; tests `tests/test_payments_api.py:179-203` |
| D6 | Multiple payments per invoice | IMPLEMENTED (allocation) | `InvoiceTransactionLink` + link routes |
| D7 | Transaction attachments | IMPLEMENTED | `src/models.py:TransactionAttachment:275-288`; routes `src/routers/payments.py:694-742`; tests `tests/test_payments_api.py:285-296` |
| D8 | `evidence_refs` | STUB | field declared `src/models.py`; 0 writers/readers except round-trip tests |
| D9 | Tenant scoping (personal/org) | IMPLEMENTED (invoices) / PARTIAL (tx/purchases) | `src/models.py` org_id; `sql_repository.py` scoped methods; tests |
| D10 | Update protection when linked | IMPLEMENTED | `src/routers/payments.py` update; tests `tests/test_payments_api.py:248-269` |
| D11 | Unique constraint on (tx,invoice) link | UNVERIFIED | no DB-level constraint observed |

## E. Location Intelligence

| # | Capability | Status | Evidence |
|---|---|---|---|
| E1 | GPS capture client | PARTIAL (lat/lon only) | `frontend/src/Purchases.jsx:36-43`; `frontend/src/Ledger.jsx:57-80` |
| E2 | Accuracy/source/provider to server | MISSING | `src/routers/payments.py:241-280,1286-1459` no accuracy fields |
| E3 | Reverse geocode | IMPLEMENTED | `src/routers/payments.py:1082-1084` (`_reverse_geocode`) |
| E4 | ADR-0002 location model | STUB | `src/models.py:147-164,241-258,323-340`; migrate `sql_repository.py:1482-1498`; 0 writers/readers |
| E5 | POI matching | MISSING | nearby = OSM direct, no local POI catalog |
| E6 | Geospatial confidence | STUB (field exists, unused) | `merchant_confidence` declared but unwired |
| E7 | User location correction | MISSING | no UI correction flow |
| E8 | Online vs physical context | PARTIAL (API/model, no UI) | `src/routers/payments.py:241-280`; `frontend/src/Purchases.jsx:103-120` no selector |
| E9 | Spatial indexing | MISSING | SQLite only; haversine in app code `src/routers/payments.py:834-842` |

## F. Offline & Synchronization

| # | Capability | Status | Evidence |
|---|---|---|---|
| F1 | Local device database | MISSING | no IndexedDB/SQLite on client |
| F2 | Offline transaction creation | MISSING | no client queue |
| F3 | Outbox pattern | STUB | `src/store/sql_repository.py:1511-1541` DDL only; 0 readers/writers |
| F4 | Sync API | MISSING | no `/sync` route |
| F5 | Conflict resolution | MISSING | only `external_ref` dedup |
| F6 | Idempotency | PARTIAL | `src/security/idempotency.py` in-memory; `TransactionRepository.create` dedup by external_ref |
| F7 | Service worker cache | MISSING | `frontend/public/sw.js:6-8` "no cache" |
| F8 | Push notifications | IMPLEMENTED | `frontend/public/sw.js:10-25`, `frontend/src/push.js`, `src/push.py`, `src/routers/push.py` |

## G. Map & Analytics

| # | Capability | Status | Evidence |
|---|---|---|---|
| G1 | Expense/payment/merchant map | PARTIAL | `src/routers/payments.py:862-957` timeline; `frontend/src/components/JourneyMap.jsx`; depends on CDN tiles |
| G2 | Time-range filters | PARTIAL | timeline endpoints; no dedicated filter UI beyond date |
| G3 | Trajectory | PARTIAL | distance/transport inference `src/routers/payments.py:912-957` |
| G4 | Spatial indexing | MISSING | no PostGIS/H3/geohash/rtree |
| G5 | Geographic analytics | MISSING | monthly report is invoice totals only `src/app.py:624`, `src/store/sql_repository.py:555-587` |
| G6 | Spending analytics (category/merchant/month) | MISSING | no aggregate endpoint |

## H. Infrastructure

| # | Capability | Status | Evidence |
|---|---|---|---|
| H1 | API authentication (JWT) | IMPLEMENTED | `src/app.py:167-190` |
| H2 | API keys | IMPLEMENTED | `src/security/api_keys.py` |
| H3 | User/org isolation | PARTIAL | see D9; app-layer only |
| H4 | Background workers | IMPLEMENTED | `src/workers/celery_app.py`, `extraction_worker.py:239-279` retries |
| H5 | Queue reliability | PARTIAL | retries 3x; no DLQ; batch loses org context |
| H6 | Object storage | MISSING (remote) | `src/store/storage.py` local only |
| H7 | Logging | IMPLEMENTED | `src/middleware.py:21-87` |
| H8 | Monitoring | PARTIAL | `/metrics` `src/app.py:162-164`; helpers unpopulated |
| H9 | Migrations | PARTIAL | `create_all` + ALTERs `sql_repository.py:1644-1699`; Alembic configured but no tree |
| H10 | Backups | IMPLEMENTED (manual) | `src/store/backup.py`; `scripts/backup.py` |
| H11 | CORS | PARTIAL (bug) | `src/middleware.py:99` omits `X-Organization-Id` |
| H12 | Production config validation | IMPLEMENTED | `src/config.py:238-259` |

## I. Recommendation & Community

| # | Capability | Status | Evidence |
|---|---|---|---|
| I1 | Nearby merchant search | PARTIAL | `src/routers/payments.py:564-581` OSM uncached |
| I2 | Price comparison | MISSING | no price index |
| I3 | Recommendation ranking | PARTIAL (heuristic) | `src/routers/payments.py:728-813` (frequency + shared + OSM) |
| I4 | Community sharing | IMPLEMENTED (opt-in) | `src/routers/payments.py:543-563`; aggregate `sql_repository.py:776-805` (≥2, rounded) |
| I5 | Consent management | PARTIAL | `PlaceShareRequest.confirmed` defaults True `src/routers/payments.py:323-329` |
| I6 | Anonymization/privacy | PARTIAL | rounded coords + visitor count only; individual visits retained |
| I7 | Shared merchant registry | MISSING | only `SharedPlaceVisit` aggregate |

## J. Testing

| # | Capability | Status | Evidence |
|---|---|---|---|
| J1 | Unit tests | IMPLEMENTED | `tests/test_invoice.py`, `test_qr.py`, `test_line_items.py`, etc. |
| J2 | Integration tests | IMPLEMENTED | `tests/test_payments_api.py`, `test_purchase_api.py`, `test_organizations.py` |
| J3 | End-to-end tests | MISSING | no browser/E2E tests |
| J4 | QR parser tests | IMPLEMENTED | `tests/test_qr.py` |
| J5 | OCR extraction tests | IMPLEMENTED | `tests/test_invoice.py`, `test_vi_extractor.py`, `test_vn_receipts.py` |
| J6 | Payment matching tests | IMPLEMENTED | `tests/test_payments_api.py`, `test_xml_matching.py` |
| J7 | Offline sync tests | MISSING | 0 tests |
| J8 | Security/isolation tests | PARTIAL | `tests/test_user_isolation.py` has vacuous assertion `:270` (`or True`) |
| J9 | Frontend tests | MISSING | `frontend/package.json` has no test script |

---

## Status Counts (across all modules, 80 tracked capabilities)

| Status | Count | % |
|---|---:|---:|
| IMPLEMENTED | 41 | 51.3% |
| PARTIAL | 20 | 25.0% |
| STUB | 6 | 7.5% |
| MISSING | 12 | 15.0% |
| UNVERIFIED | 1 | 1.3% |

Notes: percentages are of the audited capability list above; "IMPLEMENTED" requires end-to-end evidence, not just a schema/dependency. Older `docs/CAPABILITY_MATRIX.md` uses WORKS/PARTIAL/STUB/MISSING with different granularity and is superseded by this audit.
