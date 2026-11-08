# Gap Analysis & Migration Plan: Invoice/Billing Web App → Local-First Expense Intelligence Platform

> Mapping giữa codebase hiện tại (`/home/khanh/ai-invoice-billing`, branch `exp1`) và thiết kế mới `Local-First + Event-Driven (Modular Monolith MVP)`.

## 0. Tóm tắt hiện trạng codebase

Dự án hiện tại là **web app Invoice & Billing** (FastAPI + React SPA), không phải mobile local-first. Quy mô: ~3k LOC backend, React 18 frontend, SQLite (server-side), Celery/arq + Redis, OCR/LLM extraction, payments ingest (SMS/email), PWA push, org multi-tenant, purchase memory + map cơ bản.

Kiến trúc hiện tại: monolith FastAPI (`src/app.py`) + routers (`auth`, `payments`, `push`, `search`, `organizations`, `api_keys`, `google/gmail oauth`) + workers (Celery/arq) + SQLite/SQLModel.

---

## 1. Gap Analysis theo tầng

### 1.1 Client Platform
| Thiết kế mục tiêu | Hiện tại | Gap |
|---|---|---|
| React Native (iOS/Android) | React 18 web SPA (Vite) | **Lớn**: toàn bộ client cần rewrite; web có thể giữ làm admin dashboard |
| Local SQLite trên thiết bị | SQLite phía server | **Lớn**: cần local DB + sync protocol |
| Android Notification Listener | Không có (web PWA push only) | **Lớn**: cần native module (Android) |
| iOS Shortcuts / Share Extension | Không có | **Lớn**: cần iOS extension + user-driven flow |
| Offline outbox / sync queue | Không có (PWA offline cache cơ bản) | **Lớn** |
| QR parser on-device | Server-side QR decode (cv2) | Medium: port sang mobile |
| On-device OCR | Server-side (pytesseract/rapidocr) | Medium: có thể giữ server OCR cho MVP |

### 1.2 Data Model — Location (gap lớn nhất)
Thiết kế yêu cầu phân biệt 3 lớp vị trí; hiện tại trộn lẫn.

| Thiết kế | Hiện tại (models.py) | Gap |
|---|---|---|
| `PaymentLocation` (GPS at capture time, timestamp, accuracy, source) | `PaymentTransaction.location_lat/lon/captured_at` (không có accuracy/source) | Thiếu `accuracy_m`, `source`, `captured_at` chuẩn ISO |
| `MerchantLocation` (POI coords, name, address, source, confidence) | Không có model riêng; `location_name/address` trên Invoice/Purchase | Thiếu confidence, source, POI id |
| `PurchaseContext` (online/physical, evidence, user confirm/edit) | `Purchase` có `location_*` nhưng không có `context_type`/`user_confirmed` | Thiếu trường |
| Invoice location | `Invoice.location_lat/lon/name/address/transport_mode/route_sequence` | Có nhưng không tách payment vs merchant |
| Purchase location | `Purchase.location_lat/lon/name/address/captured_at` | Thiếu accuracy/source/context |

**Mô hình hiện tại**: location là một bộ trường đơn trên mỗi entity. Cần refactor thành **embedded location objects** hoặc bảng `locations` + FK, để mỗi transaction có thể có cả `payment_location` và `merchant_location`.

### 1.3 Spatial Intelligence
| Thiết kế | Hiện tại | Gap |
|---|---|---|
| PostgreSQL + PostGIS | SQLite (server) | **Lớn**: migrate DB engine |
| H3 spatial index | Không có | Chưa có; có thể dùng `h3` lib sau |
| POI Matching Engine + confidence | `nearby_places` (OSM lookup) + `places/suggestions` heuristic | Chưa có matching logic thực sự |
| Trajectory Query Engine | `get_timeline` + distance/transport inference | Có cơ bản, chưa trajectory analytics |
| Personal Expense Map / Community Map | Leaflet map trong Purchases + JourneyMap | UI có, backend spatial query yếu |

### 1.4 Backend Architecture
| Thiết kế | Hiện tại | Gap |
|---|---|---|
| Modular Monolith (rõ module) | FastAPI app lớn, routers import trực tiếp repos | Cần tách module boundaries (domain, application, infra) |
| Task queue (RabbitMQ đề xuất) | arq + Redis (Celery legacy) | Medium: có thể giữ Redis/arq cho MVP, RabbitMQ khi scale |
| API Gateway / FastAPI | FastAPI trực tiếp | OK cho MVP |
| Object storage S3-compatible | Local file storage (`LocalFileStorage`) | Medium: thêm S3 adapter |
| Dead Letter Queue | Không có | Chưa có |
| Auth/AuthZ | JWT + org context | Có, tái sử dụng |

### 1.5 AI Vision Pipeline
| Thiết kế | Hiện tại | Gap |
|---|---|---|
| Tách 2 nhánh: Receipt OCR vs Product/Food Image vs Payment Screenshot | `extractor.py` chung; `image-draft` cho purchase | Medium: cần tách router/worker rõ ràng |
| Receipt: subtotal/discount/fees/total_due | Có extract total, tax, discount | Có, tái sử dụng |
| Merchant/address/items extraction | Có | Có |
| Payment screenshot parser (paid_amount/time/provider) | `payments/ingest` parse SMS/text | Có parse text, chưa parse ảnh screenshot |
| Evidence normalization + transaction matching | `match_invoice_saved` + link repos | Có cơ bản |
| Confidence & validation gate | FieldProvenance + review UI | Có, tốt |

### 1.6 Community & Privacy
| Thiết kế | Hiện tại | Gap |
|---|---|---|
| Consent gate + anonymization + aggregation | `SharedPlaceVisit` + `UserPaymentPreference.share_place_visits` + list_shared_places (≥3 users) | Có khung cơ bản; cần consent flow rõ, anonymization pipeline |
| Community map (price/rating) | Không có price/rating index | Chưa có |
| Recommendation engine (price/distance/rating weighted) | `places/suggestions` heuristic | Chưa có scoring chuẩn |

### 1.7 Performance targets
Hiện tại chưa có benchmark; DB SQLite, map render Leaflet. Cần đo và đạt mục tiêu p95.

---

## 2. Reusable Assets (giữ)

### Backend (giữ nguyên, refactor ranh giới)
- `src/auth/*` — JWT, password, OAuth accounts, refresh tokens.
- `src/models.py` — User, Invoice, PaymentTransaction, Purchase, attachments, org, push subscription, email verification.
- `src/store/sql_repository.py` — repositories (Invoice, Transaction, Purchase, User, Org, Gmail, Push).
- `src/extract/extractor.py` — regex + OCR + LLM fallback + FieldProvenance + QR decode.
- `src/upload.py`, `src/store/storage.py` — upload validation + storage.
- `src/mail.py`, `src/push.py` — email + web push.
- `src/workers/*` — Celery/arq workers.
- `src/routers/payments.py` — transactions, timeline, places, purchases, image-draft.
- `src/purchase_memory.py` — dHash + embedding memory.
- `src/middleware.py`, `src/errors.py`, `src/config.py` — infra cross-cutting.

### Frontend (giữ làm admin/web dashboard)
- React SPA hiện tại có thể giữ cho web management; mobile client mới là React Native.
- Design tokens (`design.md`) có thể tái sử dụng cho web.

### Tests
- `tests/test_invoice.py`, `test_payments_api.py`, `test_purchase_api.py`, `test_review.py` — giữ làm regression cho backend.

---

## 3. Cần thay thế / thêm mới

### 3.1 Mobile Client (mới, Phase 1)
- React Native app với local SQLite (WatermelonDB/expo-sqlite).
- Capture Manager: manual transaction, QR scan, photo capture, manual input.
- GPS capture với `timestamp`, `accuracy`, `source` (GPS/network/fused).
- Offline outbox + sync.
- Android Notification Listener (native module) — phase sau (Phase 2+).
- iOS: Share Extension + manual actions.

### 3.2 Data Model Refactor (Phase 1-2)
- Tách location thành 3 lớp:
  - `PaymentLocation`: lat, lon, captured_at, accuracy_m, source, provider.
  - `MerchantLocation`: poi_id, name, address, lat, lon, source, confidence, verified_by_user.
  - `PurchaseContext`: type (online/physical), evidence_refs, user_confirmed.
- Thêm vào `PaymentTransaction`, `Purchase`, `Invoice`.
- Migration: thêm cột + backfill từ `location_*` cũ.

### 3.3 Spatial Backend (Phase 3)
- Migrate SQLite → PostgreSQL (+ PostGIS) cho production; giữ SQLite cho dev/test.
- Thêm H3 index (`h3` lib) cho merchant location.
- POI matching service: spatial candidate search (PostGIS ST_DWithin) → name/address similarity → confidence score.
- Trajectory query engine: time-ordered points, transport inference, distance.

### 3.4 Event Processing (Phase 2-3)
- Chuẩn hóa event schema: `TransactionCreated`, `EvidenceUploaded`, `OcrCompleted`, `EntityResolved`.
- Outbox pattern: client → API → outbox table → worker → PostGIS.
- Dead letter queue + retry policy.
- Giữ arq/Redis cho MVP; RabbitMQ khi scale.

### 3.5 Object Storage (Phase 2)
- S3-compatible adapter (boto3) với fallback local.

### 3.6 Community/Privacy (Phase 5)
- Consent flow explicit.
- Anonymization pipeline (remove PII, aggregate).
- Price/rating index + recommendation scoring.

---

## 4. Migration Plan theo Phase

### Phase 0 — Foundation & Decisions (1-2 tuần)
**Mục tiêu**: chốt scope, không code lớn.

Tasks:
1. Quyết định: Android-first vs cross-platform; web dashboard giữ hay bỏ.
2. Quyết định DB: PostgreSQL+PostGIS production, SQLite dev/test.
3. Quyết định queue: giữ arq/Redis MVP, RabbitMQ sau.
4. Thiết kế sync protocol (client outbox ↔ server).
5. Thiết kế event schema.
6. Viết ADR (Architecture Decision Records) trong `docs/`.

Deliverables: ADR docs, sync protocol spec, event schema.

### Phase 1 — Transaction Capture (MVP core, 3-4 tuần)
**Mục tiêu**: local-first transaction capture + sync.

Backend:
- Refactor location model: thêm `PaymentLocation` fields (accuracy_m, source, provider) vào `PaymentTransaction`, `Purchase`, `Invoice`.
- Migration script (alembic) thêm cột + backfill.
- Sync endpoint: `POST /sync/transactions` (batch upsert from client outbox), `GET /sync/changes?since=`.
- Outbox table server-side: `sync_outbox` (id, entity_type, entity_id, operation, payload, status, retry_count, created_at).
- Giữ manual transaction + QR + photo + manual input.

Mobile (React Native):
- Project setup (Expo hoặc bare RN).
- Local SQLite schema mirror (transactions, purchases, invoices, outbox).
- Capture Manager: manual form, QR scan, photo capture.
- GPS capture với timestamp/accuracy/source.
- Offline outbox + sync client.
- Basic map (react-native-maps) với payment location markers.

Tests:
- Backend: sync endpoint tests, migration tests.
- Mobile: unit tests cho outbox, GPS capture.

Keep: auth, extraction, payments ingest, push (web).

### Phase 2 — Evidence Intelligence (3-4 tuần)
**Mục tiêu**: OCR/vision + matching.

Backend:
- Tách vision pipeline: `ReceiptOcrWorker`, `ProductDetectionWorker`, `PaymentScreenshotParser`.
- S3 adapter cho object storage.
- Event-driven: `EvidenceUploaded` → queue → OCR → `OcrCompleted` → normalization → `EntityResolved`.
- Payment screenshot parser (amount/time/provider).
- Confidence gate + user review (đã có UI review).
- Dead letter queue + retry.

Mobile:
- Photo capture + upload to evidence.
- Review UI cho uncertain fields.
- Offline: store evidence locally, sync when online.

Keep: existing extractor, QR decode, LLM fallback.

### Phase 3 — Spatial Intelligence (3-4 tuần)
**Mục tiêu**: POI matching + personal map.

Backend:
- Migrate to PostgreSQL + PostGIS (production); SQLite vẫn cho dev/test.
- H3 index integration.
- POI matching service: spatial candidates (ST_DWithin 50-200m) → name/address similarity → confidence.
- Merchant location resolution flow: auto-match (high), suggest (medium), unresolved (low).
- Trajectory query engine: time-ordered points, transport inference.
- Personal expense map API: filter by date/area, merchant clustering.

Mobile:
- Merchant location display (prioritize merchant over payment).
- Map filters (date range, category).
- Location correction UI (user edit merchant location).

Tests: POI matching benchmarks, spatial query perf.

### Phase 4 — Analytics & Recommendation (2-3 tuần)
**Mục tiêu**: trajectory, frequency, price compare, location recommend.

Backend:
- Trajectory analytics: distance, transport mode, time patterns.
- Purchase frequency by merchant/category.
- Price comparison by store.
- Recommendation scoring: `Score = w1*S_price + w2*S_distance + w3*S_rating`, normalized.
- H3-based nearby recommendations.

Mobile:
- Analytics dashboard.
- Location recommendations UI.

### Phase 5 — Community (2-3 tuần)
**Mục tiêu**: voluntary sharing, anonymization, community map.

Backend:
- Consent flow (explicit opt-in per data type).
- Anonymization pipeline (strip PII, aggregate by H3 cell + merchant).
- Community price/rating index.
- Privacy filter middleware.
- Rate limiting + abuse protection.

Mobile:
- Consent UI.
- Community map view (aggregated, anonymized).

---

## 5. File/Model Migration Mapping (concrete)

### Models (`src/models.py`)
| Hiện tại | Hành động |
|---|---|
| `PaymentTransaction.location_lat/lon/captured_at` | Thêm `payment_location_accuracy_m`, `payment_location_source`, `payment_location_provider`, `payment_location_captured_at` (rename) |
| (new) | Thêm `MerchantLocation` embedded/bảng: `merchant_poi_id`, `merchant_name`, `merchant_address`, `merchant_lat`, `merchant_lon`, `merchant_location_source`, `merchant_location_confidence`, `merchant_verified_by_user` |
| (new) | Thêm `PurchaseContext`: `purchase_context_type` (online/physical), `evidence_refs` (JSON), `user_confirmed_location` |
| `Purchase.location_*` | Tách thành payment_location + merchant_location + context |
| `Invoice.location_*` | Tách tương tự; Invoice thường không có payment location, chỉ merchant |
| `SharedPlaceVisit` | Giữ, mở rộng với consent/anonymization fields |
| (new) | `SyncOutbox`, `SyncCursor` tables |

### Repositories (`src/store/sql_repository.py`)
| Hiện tại | Hành động |
|---|---|
| `TransactionRepository` | Thêm location fields; thêm sync methods |
| `PurchaseRepository` | Tương tự |
| (new) | `LocationRepository` (POI matching, H3) |
| (new) | `SyncRepository` (outbox, cursor) |
| `add_shared_place_visit` / `list_shared_places` | Mở rộng với consent + anonymization |

### Routers
| Hiện tại | Hành động |
|---|---|
| `src/routers/payments.py` | Giữ; thêm sync endpoints, location correction endpoints, POI match endpoints |
| (new) | `src/routers/sync.py` |
| (new) | `src/routers/locations.py` |
| (new) | `src/routers/community.py` |

### Workers
| Hiện tại | Hành động |
|---|---|
| `src/workers/extraction_worker.py` | Tách thành ReceiptOcrWorker, ProductDetectionWorker, PaymentScreenshotParser |
| `src/workers/gmail_worker.py` | Giữ |
| (new) | `src/workers/sync_worker.py` (outbox processing) |
| (new) | `src/workers/poi_matching_worker.py` |

### Frontend
| Hiện tại | Hành động |
|---|---|
| React web SPA | Giữ làm admin/web dashboard |
| (new) | React Native app (mobile client) |

### DB
| Hiện tại | Hành động |
|---|---|
| SQLite (server) | Giữ cho dev/test; thêm PostgreSQL+PostGIS cho production |
| Alembic migrations | Thêm migration cho location refactor + new tables |

---

## 6. Rủi ro & Lưu ý

1. **DB migration**: SQLite → PostgreSQL không trivial; cần abstraction layer hoặc dual-mode. Đề xuất: giữ SQLModel/SQLAlchemy, thêm `DATABASE_URL` env; dev dùng SQLite, prod dùng Postgres.
2. **Sync conflict resolution**: cần định nghĩa rõ (last-write-wins? vector clock? user-confirm?). Chốt ở Phase 0.
3. **iOS limitations**: không có auto notification capture; cần thiết kế UX dựa trên share sheet + manual. Không nên hứa auto-capture trên iOS.
4. **GPS accuracy**: `getCurrentPosition` trên web/mobile trả accuracy; cần lưu và hiển thị uncertainty. POI matching không nên auto-match khi accuracy > 50m.
5. **50m radius không đủ**: POI matching phải kết hợp name/address/time/confidence, không chỉ distance.
6. **Microservices premature**: giữ modular monolith; chỉ tách worker khi tải tăng.
7. **Community data**: không auto-public; cần consent explicit + anonymization + k-anonymity (≥3 users per cell).

---

## 7. Quyết định cần chốt (Open Questions)

1. Android-first hay cross-platform ngay?
2. Web dashboard giữ hay retire?
3. Sync conflict resolution strategy?
4. POI data source (OSM? Google Places? tự xây)?
5. Consent granularity (per-transaction? per-merchant? global toggle)?
6. Mobile framework: Expo hay bare RN?
7. Local DB: expo-sqlite vs WatermelonDB vs Realm?

---

## 8. Recommended Next Step

Nếu bạn đồng ý, tôi sẽ:
1. Tạo ADR docs cho các quyết định Phase 0.
2. Viết migration script cho location refactor (Phase 1) — giữ backward compat.
3. Scaffold sync endpoint + outbox table.
4. Scaffold React Native project skeleton (nếu chọn).

Bạn muốn bắt đầu từ bước nào?
