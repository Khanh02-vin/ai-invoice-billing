# Current Architecture Audit

**Project:** `/home/khanh/ai-invoice-billing` (branch `exp1`, dirty working tree, base commit `bfcc644`)
**Audit date:** 2026-11-08
**Method:** read-only code inspection + safe backend test run (`352 passed`)
**Target:** Local-First Expense Intelligence Platform (per `docs/adr/ADR-0001…0003`)

---

## 1. Runtime Shape

- **Backend:** FastAPI modular monolith in `src/app.py:83-160` — settings load, production readiness validation, global DB/repositories, middleware, error handlers, routers, `/metrics`, `/health`, `/ready`, static frontend.
- **Routers registered:** `src/app.py:125-132` → search, auth extension, push, payments, API keys, organizations, Google OAuth, Gmail OAuth.
- **Frontend:** React 18 + Vite SPA (`frontend/src/*`); API calls centralized in `frontend/src/api.js:17-35` (Bearer token + `X-Organization-Id`).
- **Workers:** Celery app `src/workers/celery_app.py:32-117` (Redis broker/result backend, JSON serialization, retries/timeouts, task routes, beat schedule). Note: `pyproject.toml:29-31` declares `arq`/`redis` while code uses Celery — packaging inconsistency.
- **Storage:** local filesystem only (`src/store/storage.py` `LocalFileStorage` behind `StorageBackend`).
- **DB:** SQLite only in active config (`src/config.py:209` `database_path = "invoices.db"`, `src/store/db.py`). Postgres/PostGIS exists only in docs ADR-0003.
- **Async extraction:** `src/app.py:512-644` stores file, enqueues Celery task, or runs `_do_extract` synchronously when no broker.

## 2. Layer Map

| Layer | Key files | Notes |
|---|---|---|
| App composition | `src/app.py` | auth, org, repos singletons |
| Domain models | `src/domain/models.py`, `src/models.py` | Invoice, PaymentTransaction, Purchase, links, attachments, orgs, users |
| Routers | `src/routers/*.py` | payments is the largest (transactions, timeline, places, purchases, image-draft, camera-upload) |
| Services | `src/payments/service.py`, `src/payments/matching.py`, `src/purchase_memory.py`, `src/extract/*` | |
| Store | `src/store/sql_repository.py`, `repository.py`, `db.py`, `storage.py`, `backup.py` | `create_all` + idempotent ALTERs, no Alembic tree |
| Workers | `src/workers/extraction_worker.py`, `celery_app.py`, `gmail_worker.py` | |
| Infra | `src/middleware.py`, `src/errors.py`, `src/config.py`, `src/observability/metrics.py`, `src/push.py`, `src/mail.py` | |
| Frontend | `frontend/src/*` | no test suite |

## 3. Authentication & Session

- JWT bearer `src/app.py:167-190` (`current_user`); optional `X-API-Key` `src/security/api_keys.py:74-128`.
- Password hashing, refresh tokens, email verification/reset `src/auth/security.py`, `src/routers/auth_ext.py`.
- Google/Gmail OAuth routes `src/routers/google_oauth.py`, `src/routers/gmail_oauth.py`.
- API keys: random `sk_live_...`, only SHA-256 hash+prefix persisted, plaintext returned once (`src/security/api_keys.py:49-56`).
- Scope rules: `src/security/api_keys.py:33-44` (invoices/payments/reports; admin requires JWT).
- Rate-limit dependencies `src/security/api_rate_limit.py`, `src/config.py:180-193`.
- Risks: CORS `allow_headers` omits `X-Organization-Id` (`src/middleware.py:99`) while frontend always sends it (`frontend/src/api.js:24-25`); `RateLimitMiddleware` not registered (`src/app.py:100-104`); best-effort `last_used_at` (`src/security/api_keys.py:105-111`).

## 4. Multi-Tenancy

- `current_organization` `src/app.py:204-217` validates membership and role.
- Invoice repo enforces org-or-user scoping `src/store/sql_repository.py:423-564`; duplicate detection scoped `384-405`.
- Org router membership/admin/owner checks `src/routers/organizations.py:78-178`.
- Frontend org state `frontend/src/Invoices.jsx:130-225`, `frontend/src/api.js:24-25`.
- Risks: application-layer only (no row-level security); transaction/purchase/link repos have user-only or mixed scoping (`sql_repository.py:629-654,782-799,930-956,965-1032`); `extract_batch` loses org context; latent `_can_access` helper `src/app.py:310-312` only checks `user_id`.

## 5. Extraction & Evidence

- `extract_invoice` `src/extract/extractor.py:722-745` calls `_extract_text_with_layout`, then `extract_from_text(..., llm=get_llm_provider(), layout=layout)` — LLM fallback IS wired in current code (contrary to older docs).
- OCR fallback chain: PaddleOCR → RapidOCR → Tesseract; PDF text layer then scanned OCR; layout-aware total selection `_pick_total_layout`.
- QR decode `_qr_decode` + `_apply_qr_crosscheck` for invoice QR only.
- XML e-invoice parser `src/extract/xml_parser.py`, invoked in worker `src/workers/extraction_worker.py:104-109`.
- Provenance per field `src/domain/models.py:FieldProvenance`; review corrections `src/store/sql_repository.py:InvoiceRepository.update:454-552`.
- Line items `_extract_line_items` with qty×price≈amount validation.

## 6. Payments & Matching

- Manual transactions `src/routers/payments.py:402-439` (create), `src/payments/service.py:ingest:46-95` auto-matches unique.
- Matching `src/payments/matching.py:amounts_match/pick_invoices_decision` amount/currency with tolerance; combined subset-sum up to 6.
- Manual link/unlink `src/routers/payments.py:548-691` with allocation amounts.
- IMAP poller `src/payments/imap_poller.py:215-322`; parser `src/payments/parser.py`.
- Gmail metadata-only alerts `src/workers/gmail_worker.py:112-183`.
- Dedup by external_ref / Gmail message / checksum.
- Attachments stored, not parsed.
- Product-image draft `src/routers/payments.py:1210-1283` (memory + image LLM, no amount inference).

## 7. Location

- ADR-0002 fields exist on `Invoice`/`PaymentTransaction`/`Purchase` (`src/models.py:147-164,241-258,323-340`) and migrated (`src/store/sql_repository.py:1482-1498`).
- **0 writers, 0 readers** — stub.
- Client sends lat/lon (+ captured_at in Ledger) only; no accuracy/source/provider.
- Reverse geocode `_reverse_geocode`; haversine distance; nearby places via OSM Nominatim (uncached).
- Timeline `src/routers/payments.py:862-957`; JourneyMap Leaflet (CDN tiles).

## 8. Offline / Sync

- `sync_outbox`/`sync_cursor` DDL `src/store/sql_repository.py:1511-1541`, **0 consumers/writers**.
- No `/sync` route; no client queue/IndexedDB; SW does not cache (`frontend/public/sw.js:6-8`).
- Push notifications work (`sw.js:10-25`, `frontend/src/push.js`, `src/push.py`, `src/routers/push.py`).

## 9. Map & Analytics

- Timeline map works; distance/transport inference.
- Monthly invoice report `src/app.py:624` + PDF `src/reports/pdf_exporter.py`.
- No geographic spend analytics, no PostGIS/H3, no spatial index.

## 10. Community / Recommendation

- Place suggestions heuristic `src/routers/payments.py:728-813` (frequency + shared + OSM), not ML.
- Opt-in sharing `src/routers/payments.py:543-563`; anonymized aggregate `sql_repository.py:776-805` (≥2 contributors, rounded coords).
- No price/rating engine; `purchase_memory.py` is recognition, not recommendation.

## 11. Infrastructure

- Auth: JWT + API keys; org isolation application-layer.
- Workers: Celery retries 3x; batch task loses org context.
- Storage: local only; backup/restore `src/store/backup.py` with tests.
- Logging: request ID + security headers + structured access log `src/middleware.py:21-87`; `/metrics` `src/app.py:162-164` but metric helpers unpopulated.
- Migrations: `create_all` + idempotent ALTERs; Alembic configured but no tree.
- Deploy: Docker Compose, K8s with non-root/read-only/seccomp/PVC; CI `.github/workflows/ci.yml`.
- Config fail-fast `src/config.py:238-259`.

## 12. Testing

- Backend: pytest, 352 passed this run (34 warnings). Frontend: no tests. See `TEST_BASELINE.md`.
- Vacuous assertion `tests/test_user_isolation.py:270` (`or True`).

## 13. Known Doc/Code Drift

- Older docs (`docs/CAPABILITY_MATRIX.md`, `docs/GAP_ANALYSIS.md`) claim LLM fallback unwired; current code wires it. Treat those docs as unverified.
- `docs/gap-analysis-local-first.md` says ~300 passed; actual 352.
- ADR-0002 location model is schema-only in code.
- `evidence_refs` is a dead column (0 writers/readers) contrary to any "evidence" implication.
- `sync_outbox`/`sync_cursor` DDL-only.
- Service worker explicitly does not cache.
- `frontend/public/sw.js` push-only.
- Debug `print` in `src/app.py:569`, `src/workers/extraction_worker.py:83,123`.

## 14. Overall Classification Summary

- Implemented (core product): transaction capture, invoice extraction/OCR/QR, matching, IMAP/Gmail ingest, push, auth, org scoping (invoices), storage, backup, timeline.
- Partial: location model, LLM wiring (now wired, docs stale), nearby places, reports/analytics, recommendations, community.
- Stub: ADR-0002 location fields, `evidence_refs`, sync tables.
- Missing: offline sync, mobile client, PostGIS/H3, payment QR, payment screenshot parsing, native notification capture, analytics engine, price/rating engine, frontend tests.
- Unverified: full multi-tenant safety across all repo paths, production secrets, backup scheduling.
