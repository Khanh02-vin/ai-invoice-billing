# Architecture Audit Report — AI Invoice & Billing

- **Ngày audit:** 2026-09-10
- **Phạm vi:** source code, tests, Docker/Compose, Kubernetes, CI, scripts và tài liệu trong repository hiện tại.
- **Nguyên tắc:** mọi kết luận được gắn `VERIFIED`, `INFERENCE` hoặc `UNKNOWN`; không thay đổi code trong quá trình audit.

## 1. Tóm tắt điều hành

### Hệ thống dùng để làm gì?

`VERIFIED`: Đây là web application quản lý hóa đơn: nhận PDF/ảnh/text, trích xuất trường hóa đơn bằng heuristic/OCR và tùy chọn LLM fallback, lưu dữ liệu, hỗ trợ cập nhật/trạng thái và báo cáo tháng. README mô tả luồng `upload → trích xuất → lưu trữ → báo cáo` tại `README.md:1-17`; các route chính nằm trong `src/app.py:123-280`.

`VERIFIED`: Hệ thống hiện có thêm authentication JWT, xác minh email, refresh/revoke token, MFA, tổ chức/RBAC, tìm kiếm vector và billing/subscription. Các router được include tại `src/app.py:55-59`.

`INFERENCE`: Đây là một modular monolith hơn là microservices: FastAPI, SQLite và filesystem chạy cùng process; các chức năng OCR/LLM/PDF/billing được gọi trong request path.

### Kết luận rủi ro chính

1. **P0/P1 — Dữ liệu production có thể mất khi pod thay thế:** Kubernetes Deployment dùng SQLite/local filesystem nhưng không khai báo PVC; HPA tồn tại dù state không chia sẻ (`k8s/deployment.yaml:1-16,35-80`, `k8s/hpa.yaml`).
2. **P1 — Test và authorization không nhất quán:** `.venv/bin/python -m pytest -q` cho **102 passed, 4 failed, 114 warnings**; batch upload và PDF export nhận 403 thay vì kết quả endpoint mong đợi (`tests/test_batch.py`, `tests/test_pdf_export.py`).
3. **P1 — Không có queue/worker:** OCR, batch, LLM fallback và PDF chạy trong process API; request dài có thể chiếm worker và không có retry/durability phân tán.
4. **P1 — Deployment image chưa có supply/publish pipeline:** K8s tham chiếu `ai-invoice-billing-backend:latest`, nhưng CI không build/push image.
5. **P2 — Backup chỉ là thủ công:** có CLI backup/restore nhưng không có lịch, retention, off-site copy hoặc restore drill tự động.
6. **P2 — Observability chưa khép kín:** có `/metrics`, Prometheus config và dashboard nhưng không có Prometheus/Grafana runtime hoặc alerting deployment.

Không có bằng chứng trong repository cho thấy hệ thống đã production-ready end-to-end.

## 2. Xác định hệ thống

| Hạng mục | Kết luận | Bằng chứng | Chắc chắn |
|---|---|---|---|
| Tên/mục đích | Invoice & Billing System; quản lý và trích xuất hóa đơn | `README.md:1-17`; `src/app.py:44` | VERIFIED |
| Loại ứng dụng | HTTP API + React SPA; có thêm CLI demo và scripts vận hành | `src/app.py:44`; `frontend/package.json`; `main.py:1-56`; `scripts/` | VERIFIED |
| Backend | Python 3.12, FastAPI, Uvicorn | `requirements.txt:1-3`; `Dockerfile:3,53` | VERIFIED |
| Frontend | React/Vite development server | `frontend/package.json`; `docker-compose.yml:40-55` | VERIFIED |
| Database | SQLite, mặc định `invoices.db`; nhiều repository dùng cùng file DB | `src/store/db.py:8-12`; `src/store/repository.py:25-58` | VERIFIED |
| File storage | Local filesystem qua `LocalFileStorage`; key bị basename hóa | `src/app.py:70`; `src/store/storage.py:50-69` | VERIFIED |
| OCR/PDF | PDF parsing và tùy chọn OCR; runtime Docker cài `libmagic1`, `tesseract-ocr` | `requirements.txt:5,9-12`; `Dockerfile:30-33`; `src/extract/extractor.py` | VERIFIED |
| LLM | OpenAI-compatible provider, mặc định endpoint/model qwencoder; dùng `LLM_BASE_URL`, `LLM_API_KEY`/`OPENAI_API_KEY`, `LLM_MODEL` | `src/llm/base.py:23-50` | VERIFIED |
| Auth | JWT HS256 mặc định, PBKDF2 password hash; email verification được yêu cầu ở protected routes | `src/config.py:39-43`; `src/app.py:74-84`; `src/auth/security.py` | VERIFIED |
| Billing | Provider abstraction mock/Stripe/Paddle configuration; webhook routes | `src/routers/billing.py:1-7`; `src/billing/provider.py`; `.env.example:37-40` | VERIFIED |
| Queue/cache | Không thấy Celery/RQ/Redis/RabbitMQ/Kafka hoặc worker/scheduler; vector store là in-process | toàn repository; `src/search/vector_store.py` | VERIFIED |
| External services | LLM endpoint; tùy chọn SMTP; billing provider; Prometheus scrape target | `src/llm/base.py:34-50`; `src/config.py:49-...`; `src/billing/`; `k8s/servicemonitor.yaml` | VERIFIED |
| Local start | `uvicorn main:app --reload --port 8000`; frontend `npm run dev`; Compose | `README.md:52-72`; `docker-compose.yml:4-9,40-55` | VERIFIED |
| Test | `pytest tests/ -v`; thực tế cần `.venv/bin/python -m pytest` trong môi trường này | `README.md:111`; audit command | VERIFIED |
| Deploy | Docker Compose; Dockerfile; K8s manifests; CI chỉ test/lint/config validation | `Dockerfile`; `docker-compose.yml`; `k8s/`; `.github/workflows/ci.yml` | VERIFIED |

### Giả định chưa xác minh

- `UNKNOWN`: chưa có bằng chứng về production database ngoài SQLite, object storage thật, SMTP đang hoạt động, billing provider thật hoặc LLM credentials hợp lệ.
- `UNKNOWN`: chưa có bằng chứng về schema migration tool/versioned migration ngoài lazy `CREATE TABLE`/`ALTER` trong repository.
- `UNKNOWN`: chưa có bằng chứng frontend production được serve từ artifact; Compose chạy Vite dev server.
- `UNKNOWN`: chưa có bằng chứng K8s image `ai-invoice-billing-backend:latest` đã được build/push tới registry hoặc có mặt trên node.
- `INFERENCE`: do runtime xử lý trong request path và không có worker, OCR/LLM có thể làm tăng latency; benchmark production chưa được cung cấp.

## 3. Bản đồ repository

| Thành phần | File/thư mục | Vai trò | Bằng chứng | Mức chắc chắn |
|---|---|---|---|---|
| Entry point API | `src/app.py:44-59` | Khởi tạo FastAPI, middleware, routers | `app = FastAPI`; `include_router` | VERIFIED |
| Entry point compatibility | `main.py:1-...` | Demo extraction/repository, không phải ASGI wrapper chính theo nội dung đọc được | `main.py:1-8,60-...` | VERIFIED |
| Auth routes | `src/app.py:123-153` | register/login/me | decorators và handler bodies | VERIFIED |
| Invoice routes | `src/app.py:162-265` | list/get/create/update/delete/upload/report/pdf | route decorators | VERIFIED |
| Organization/RBAC | `src/routers/orgs.py:24-43` | org membership, role checks | `APIRouter(prefix="/orgs")`, local auth | VERIFIED |
| Search | `src/routers/search.py:19-56` | caller-scoped vector search, lazy indexing | route and `index_many/search` calls | VERIFIED |
| Billing | `src/routers/billing.py:27-...` | checkout, subscription, webhook, entitlement | route module docstring and handlers | VERIFIED |
| Extended auth | `src/routers/auth_ext.py:1-...` | refresh/revoke, email/password reset, MFA | module docstring and routes | VERIFIED |
| Controller/handler | `src/app.py`, `src/routers/*.py` | HTTP orchestration/dependency checks | handler bodies | VERIFIED |
| Extraction service | `src/extract/extractor.py` | regex, locale parsing, OCR/LLM fallback | module and functions | VERIFIED |
| Domain model | `src/domain/models.py`, `src/domain/billing.py`, `src/domain/orgs.py` | Pydantic/domain objects, statuses, roles | model definitions | VERIFIED |
| Invoice repository | `src/store/repository.py:14-58` | SQLite CRUD/report/schema migration | class and SQL | VERIFIED |
| User repository | `src/store/users.py:11-45` | users/auth token persistence | class and schema setup | VERIFIED |
| Billing repository | `src/billing/repository.py` | subscriptions/webhook persistence | module | VERIFIED |
| DB abstraction | `src/store/db.py:8-40` | connection/commit/schema hook | `SQLiteRepo` | VERIFIED |
| File storage | `src/store/storage.py:17-69` | local object interface, checksum | `LocalFileStorage` | VERIFIED |
| Upload validation | `src/upload.py:1-...` | size/MIME/PDF pages/malware checks | module docstring/functions | VERIFIED |
| Auth security | `src/auth/security.py`, `src/auth/tokens.py`, `src/auth/mfa.py` | password/JWT/refresh/MFA primitives | definitions | VERIFIED |
| Observability | `src/middleware.py`, `src/observability/metrics.py` | request ID, headers, logs, metrics | middleware and metrics endpoint | VERIFIED |
| Backup/restore | `src/store/backup.py`, `scripts/backup.py`, `scripts/restore.py` | archive and restore commands | script CLI definitions | VERIFIED |
| Frontend | `frontend/src/Login.jsx`, `frontend/src/Invoices.jsx`, `frontend/src/api.js`, `frontend/src/styles.css` | auth, upload/list/report UI | files and Vite config | VERIFIED |
| Tests | `tests/test_*.py` | unit/API/security/billing/RAG/regression tests | test inventory | VERIFIED |
| Deploy | `Dockerfile`, `docker-compose.yml`, `k8s/`, `.github/workflows/ci.yml` | build/runtime/orchestration/CI | manifests | VERIFIED |

### Cây thư mục rút gọn

```text
.
├── src/
│   ├── app.py
│   ├── config.py, middleware.py, errors.py, upload.py
│   ├── auth/                  # password, JWT, refresh, MFA
│   ├── domain/                # invoice, org, money, billing models
│   ├── extract/               # regex/OCR/LLM extraction
│   ├── llm/                   # provider abstraction
│   ├── routers/               # orgs, search, billing, extended auth
│   ├── search/                # embeddings/vector store
│   ├── store/                 # SQLite repositories, storage, backup
│   ├── billing/               # provider, entitlement, persistence
│   ├── security/              # rate limit/malware/ops controls
│   └── observability/
├── frontend/src/              # React SPA
├── tests/                     # API, extraction, auth, billing, RAG, security
├── k8s/                       # Deployment, Service, Ingress, HPA, probes
├── monitoring/                # Prometheus/dashboard config
├── scripts/                   # backup/restore/RAG evaluation
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .github/workflows/ci.yml
```

## 4. Sơ đồ kiến trúc và luồng dữ liệu

```mermaid
flowchart LR
    Browser[React SPA :5173] -->|Bearer JWT / multipart| API[FastAPI :8000]
    API --> MW[Request ID / headers / logs / CORS]
    MW --> Auth[Auth + email verification + RBAC]
    Auth --> Routes[Invoice / org / search / billing / auth-ext routes]
    Routes --> Extract[Regex + PDF parser + optional OCR + optional LLM]
    Routes --> Repo[SQLite repositories]
    Routes --> Storage[LocalFileStorage]
    Routes --> Vector[In-process embedding/vector store]
    Routes --> Billing[Mock/Stripe/Paddle provider]
    Routes --> SMTP[Optional SMTP]
    Extract --> LLM[OpenAI-compatible external endpoint]
    Repo --> SQLite[(invoices.db / other SQLite tables)]
    Storage --> Files[(local data/storage)]
    API --> Metrics[/metrics, /health, /ready]
```

### Luồng upload hóa đơn

1. Client gửi multipart tới `/upload` (`src/app.py:208-...`).
2. `current_user` decode JWT, lấy user từ `UserRepository`, từ chối user chưa verified (`src/app.py:74-84`).
3. `validate_upload` kiểm tra kích thước/MIME/số trang và malware scanner (`src/upload.py:1-...`).
4. Nội dung được gửi vào extractor; extractor parse text/PDF/OCR và có thể gọi LLM fallback (`src/extract/extractor.py`, `src/llm/base.py:34-50`).
5. Invoice được gắn user/source metadata và ghi SQLite qua `InvoiceRepository`; file gốc có thể ghi qua `LocalFileStorage` (`src/store/repository.py`, `src/store/storage.py`).
6. Response trả invoice schema.

`UNKNOWN`: chưa đủ bằng chứng trong phần đã đọc để khẳng định mọi nhánh upload luôn lưu file gốc trước hay sau khi commit invoice; cần audit đầy đủ thân hàm `/upload` và test storage integration riêng.

### Luồng truy vấn/report

- List/get/update/delete invoice đi qua repository và user scoping (`src/app.py:162-207`, `src/store/repository.py`).
- Monthly report được repository tổng hợp và `/reports/monthly/{period}` trả JSON; PDF export dùng reportlab (`src/app.py:257-280`, `src/reports/pdf_exporter.py`).
- Search lazy-index tối đa 1000 invoice của user rồi gọi in-process vector store (`src/routers/search.py:40-56`).

### Luồng billing

- Authenticated client gọi checkout/subscription/entitlement.
- Webhook `/billing/webhook/{provider}` xác minh signature rồi lưu event/provider state (`src/routers/billing.py:1-7,27-...`).
- `BILLING_PROVIDER=mock` được Compose dùng mặc định (`docker-compose.yml:28`); `.env.example` cảnh báo production không dùng mock (`.env.example:37-40`).

## 5. Đối chiếu tài liệu/tên gọi với hành vi thật

| Mismatch | Bằng chứng | Đánh giá |
|---|---|---|
| README nói SQLite persistent, nhưng K8s không cấp persistent volume | `README.md:14-16`; `k8s/deployment.yaml`; không có PVC | `VERIFIED`: persistence đúng ở local/Compose volume, chưa đúng với pod replacement |
| README mô tả app version không nhất quán | `src/app.py:44` version `3.0.0`, `/health` trả version `2.0.0` tại `src/app.py:88-91` | `VERIFIED` documentation/API mismatch |
| “Production fail-fast” nhưng Compose mặc định billing mock | `.env.example:37-40`; `docker-compose.yml:28`; `src/config.py:...` | `VERIFIED`: mock là default dev; production safety phụ thuộc env validation, cần xác minh runtime config matrix |
| Comment nói protected routes yêu cầu verified email nhưng register trả token ngay | `src/app.py:74-84,123-135` | `VERIFIED`: register có thể phát access/refresh token trước verification; token không dùng được ở protected routes. Đây có thể là chủ ý nhưng UX/contract chưa rõ |
| “Background/job status” có model/schema nhưng không có worker | `src/domain/models.py`; `src/store/repository.py:47-56`; không có queue/worker | `VERIFIED`: tên/schema không chứng minh có async processing |
| `main.py` được Dockerfile copy nhưng nội dung đọc được là demo script | `Dockerfile:38-53`; `main.py:1-8,60-...` | `VERIFIED`: container command thực tế là `src.app:app`, nên `main.py` không phải runtime ASGI entrypoint |
| Compose frontend là production deployment | `docker-compose.yml:40-55` chạy `npm install && npm run dev` | `VERIFIED`: đây là dev server, không phải static production serving |

## 6. Đánh giá khả năng chạy và kiểm thử

### Kết quả đã chạy

- `./.venv/bin/python -m pytest -q`: **102 passed, 4 failed, 114 warnings**.
- Failed:
  - `tests/test_batch.py::test_batch_upload_success`: expected at least two results, received zero.
  - `tests/test_batch.py::test_batch_too_many`: expected HTTP 400, received 403.
  - `tests/test_pdf_export.py::test_pdf_export`: expected HTTP 200, received 403.
  - `tests/test_pdf_export.py::test_pdf_no_invoices`: expected HTTP 404, received 403.
- `python -m pytest -q` không chạy trong shell hiện tại vì `python` không có trên PATH; virtualenv có interpreter.
- Frontend production build đã được agent xác minh: `npm --prefix frontend run build` thành công (`vite: build ok`).

`VERIFIED`: 4 failure trên không chứng minh endpoint production sai tuyệt đối; chúng chứng minh test setup/authorization contract hiện không nhất quán. Các 403 có khả năng xảy ra trước endpoint-specific assertion do email verification, nhưng cần trace fixture và endpoint body để kết luận nguyên nhân duy nhất.

`VERIFIED`: Không coi test thiếu là bug. Các phần chưa được test integration đầy đủ phải ghi là chưa có bằng chứng kiểm thử.

## 7. Security/data/integrity audit

### P0/P1

#### P1 — Mất dữ liệu khi K8s pod thay thế

- SQLite mặc định là file và local storage là filesystem (`src/store/db.py:8-12`, `src/store/storage.py`).
- Deployment chỉ có pod/container path, không có PVC cho `/app/data` (`k8s/deployment.yaml:1-80`).
- Pod replacement, reschedule hoặc scale có thể làm mất DB/uploads của pod cũ.
- HPA với state local làm phân mảnh dữ liệu giữa replicas (`k8s/hpa.yaml`).

#### P1 — Authorization contract làm batch/PDF không hoạt động theo test

- Protected dependency từ chối unverified user bằng 403 (`src/app.py:74-84`).
- 4 test failures nhận 403 ở batch/PDF thay vì expected endpoint response.
- Ảnh hưởng trực tiếp tới user flow và confidence deploy.

#### P1 — Synchronous expensive work

- Không có queue/worker/external job service; extraction, LLM fallback, batch và PDF được gọi trong API process.
- `src/extract/extractor.py`, `src/app.py`, `src/routers/search.py` cho thấy xử lý inline.
- `INFERENCE`: OCR/LLM/PDF chậm có thể exhaust worker; chưa có load test chứng minh mức độ.

#### P1 — External LLM data egress

- `OpenAIProvider.complete` gửi prompt/nội dung tới `LLM_BASE_URL` (`src/llm/base.py:34-50`).
- `VERIFIED`: nếu bật LLM, invoice content đi ra external endpoint.
- `UNKNOWN`: chưa thấy policy redaction, data processing agreement, tenant isolation hoặc audit log cho egress.

#### P1 — Billing webhook/idempotency cần bằng chứng concurrency

- Billing router có signature validation và persistence theo module contract (`src/routers/billing.py:1-7,27-...`).
- `UNKNOWN`: chưa có bằng chứng đủ về unique constraint/idempotency atomicity dưới concurrent duplicate delivery; test concurrency/rollback chuyên biệt chưa thấy trong kết quả audit.

### P2

- JWT default dev secret tồn tại nhưng config có production readiness check (`src/config.py:14,39-43`). Cần verify mọi production path luôn set `APP_ENV=production` và secret thật.
- Refresh/email/password reset/MFA có nhiều stateful token tables; migration code tự drop legacy plaintext-token schemas (`src/store/users.py:14-41`). Đây là hardening có chủ ý, nhưng migration destructive cần backup/rollback proof trước deploy.
- Rate limiter/security modules có test, nhưng chưa có bằng chứng distributed enforcement giữa nhiều replicas.
- `/ready` kiểm tra DB ping và storage path existence, chưa chứng minh khả năng ghi file thật hoặc dependency của external LLM/billing (`src/app.py:94-116`).

## 8. Hiệu năng, maintainability và deploy readiness

### Hiệu năng/scaling

- SQLite file DB và in-process vector index không phù hợp horizontal scale nếu không có shared state/locking strategy.
- Search lazy index gọi `invoice_repo.list(...limit=1000)` và fit index trong request đầu tiên (`src/routers/search.py:46-52`). `INFERENCE`: cold request latency tăng theo dữ liệu.
- Không có cache phân tán, queue hoặc worker pool.

### Maintainability

- Các router lặp lại `current_user_local` để tránh import cycle (`src/routers/orgs.py:26-43`, `src/routers/search.py:21-37`, `src/routers/billing.py:27-50`). Điều này làm auth behavior dễ drift so với `src/app.py:74-84`.
- Repository tự lazy-create/migrate schema tại runtime (`src/store/db.py:33-40`, `src/store/repository.py:23-...`, `src/store/users.py:14-45`), không có migration history rõ ràng.
- Version contract không đồng nhất (`3.0.0` vs `2.0.0`).
- Codebase đã có nhiều phase features; cần contract tests cho cross-module auth/org/billing/search.

### Deploy readiness

- Docker runtime non-root, healthcheck, production env hooks là điểm tốt (`Dockerfile:21-53`).
- K8s có liveness/readiness, Service/Ingress/HPA/ServiceMonitor nhưng thiếu PVC, image publishing, migration job, backup CronJob và alerting.
- CI chạy lint/test/config validation nhưng không build/push image, validate Compose/K8s, browser test, backup/restore drill hoặc deploy smoke test (`.github/workflows/ci.yml:4-76`).
- Frontend Compose dùng Vite dev server; không nên coi là production artifact.

## 9. P0/P1/P2/P3 backlog và thứ tự cải thiện

### P0 — trước khi expose production

1. Quyết định persistence architecture: chuyển DB/file uploads sang managed DB/object storage **hoặc** cấp PVC và single-writer policy; bỏ/khóa HPA khi state chưa externalized.
2. Xác minh secrets và external egress: production `JWT_SECRET`, billing webhook secret, SMTP/LLM credentials; redact/contractualize invoice data gửi LLM.
3. Chạy backup trước migration/destructive schema operations; xác minh restore được trên bản sao.

### P1 — trước release kế tiếp

4. Trace và sửa contract auth/test cho batch upload và PDF export; thêm tests đã verify email, unverified user và cross-user access.
5. Bổ sung idempotency/concurrency/rollback tests cho billing webhook và các state transition.
6. Tách OCR/LLM/batch/PDF dài khỏi request path bằng durable job design hoặc giới hạn/timeout rõ ràng; nếu chưa làm, đặt timeout/rate limit và đo latency.
7. Tạo image build/push có immutable tag/digest; K8s không dùng `latest` không kiểm soát.
8. Đưa migration thành quy trình explicit, reviewable và có rollback/backup policy.

### P2 — sau khi P1 ổn định

9. Hoàn thiện metrics: latency/error rate/queue depth/LLM calls/billing webhook outcomes; triển khai Prometheus/Grafana/alert rules.
10. Tự động backup schedule, retention, off-site destination và restore verification.
11. Bổ sung load test cho SQLite/search/upload và browser E2E cho frontend.
12. Hợp nhất auth dependency thay vì bản sao local trong routers; đồng bộ version endpoint/docs.

### P3

13. Dọn naming/comment/version inconsistencies và cập nhật README theo behavior đã xác minh.
14. Tách module chỉ khi có evidence về coupling/performance; không refactor lớn chỉ vì style.

## 10. Kết luận

`VERIFIED`: Repository đã có một nền tảng khá đầy đủ cho demo/internal invoice platform: FastAPI + React, auth hardening, extraction, reporting, billing/RAG modules, containerization và operational scaffolding.

`VERIFIED`: Repository **chưa có bằng chứng đủ để gọi là production-ready**. Blocker lớn nhất là persistence K8s, test/auth inconsistency, synchronous processing, image delivery và backup/restore operations.

`UNKNOWN`: Không thể kết luận độ chính xác nghiệp vụ thực tế, SLA, throughput, disaster recovery RPO/RTO hoặc security compliance vì chưa có production telemetry, load test, dependency contract evidence và restore drill.
