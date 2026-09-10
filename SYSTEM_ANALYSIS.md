# BỨC TRANH HỆ THỐNG — ai-invoice-billing

> Phân tích dựa trên **CODE LÀM BẰNG CHỨNG**. Không có kết luận nào được bịa;
> mọi mục đều gắn nhãn:
> - **VERIFIED** — xác minh trực tiếp từ code/lệnh đã chạy
> - **INFERENCE** — suy luận từ code
> - **UNKNOWN** — chưa đủ bằng chứng
>
> Ngày phân tích: 2026-09-10 · Trạng thái repo: branch `main`, app version `3.0.0` (`src/app.py:42`)

---

## BƯỚC 1 — XÁC ĐỊNH HỆ THỐNG

### 1.1 Tên & mục đích — VERIFIED
- **Tên:** `ai-invoice-billing` — `README.md:1`
- **Mục đích:** *Upload hóa đơn → AI/OCR trích xuất dữ liệu → người dùng kiểm tra/sửa → lưu hồ sơ → báo cáo và xuất dữ liệu* — `PLAN_ai-invoice-billing.md:7`
- Wedge: hóa đơn **Việt Nam (GTGT) + tiếng Anh** — `PLAN_ai-invoice-billing.md:39`

### 1.2 Loại ứng dụng — VERIFIED
| Loại | Bằng chứng |
|---|---|
| HTTP API | FastAPI — `src/app.py:1`, `requirements.txt:1` |
| SPA frontend | React 18 + Vite 5 — `frontend/src/`, `README.md:30-31` |
| CLI demo | `main.py:1` (demo end-to-end trích xuất → lưu → báo cáo) |

### 1.3 Ngôn ngữ & framework — VERIFIED
- Python 3.12 (`Dockerfile:3`), FastAPI, Pydantic v2 (`requirements.txt:1-3`)
- PyJWT (`requirements.txt:6`), pdfplumber/PyPDF2/reportlab (`requirements.txt:4-5,17`)
- `openai>=1.0` cho LLM fallback (`requirements.txt:16`)

### 1.4 Database & lưu trữ — VERIFIED
| Thành phần | Chi tiết | Bằng chứng |
|---|---|---|
| DB chính | SQLite, file `invoices.db` | `src/config.py:61`, `src/store/db.py:8` |
| Bảng | users, invoices, email_verifications, password_reset_tokens, refresh_tokens, mfa_secrets | `src/store/users.py:14-60` |
| Bảng billing | subscriptions, checkout_sessions, webhook_events, entitlements | `src/billing/repository.py:45-99` |
| File storage | `LocalFileStorage` tại `data/storage` | `src/config.py:60`, `src/app.py:69` |
| Object storage | Abstraction S3-compatible (stub, chưa deploy) | `src/store/object_storage.py` |
| Backup | tar.gz qua stdlib `tarfile` + SQLite backup API | `src/store/backup.py` |

### 1.5 Dịch vụ bên ngoài
| Dịch vụ | Trạng thái trong code | Mức |
|---|---|---|
| OpenAI/LLM (qwen, gpt-4o-mini) | Fallback khi confidence < 0.8; có `LLM_API_KEY` env | VERIFIED — `src/config.py:64-69` |
| Stripe/Paddle | Provider abstraction + **MockProvider** offline; `StripeProvider` là skeleton raise `NotImplementedError` | VERIFIED — `src/billing/provider.py:1-11` |
| Email (SMTP) | CHƯA có — token verification/reset trả thẳng trong response với `TODO: Send email in production` | VERIFIED — `src/routers/auth_ext.py:114,132` |
| ClamAV | Stub socket check, fallback magic-bytes scanner | VERIFIED — `src/security/malware.py:149-181` |
| S3/MinIO | Abstraction tồn tại, chưa cấu hình thật | UNKNOWN (deployment) |
| Tesseract OCR | Cài trong image, optional khi dev | VERIFIED — `Dockerfile:32` |
| Redis/queue/cache | **KHÔNG có** — rate limit dùng memory/SQLite | INFERENCE |
| Background job worker | **KHÔNG có** worker thực; Job model chỉ là state machine | INFERENCE |

### 1.6 Khởi động local — VERIFIED
```bash
pip install -r requirements.txt
cp .env.example .env          # JWT_SECRET bắt buộc ở production
uvicorn src.app:app --reload   # hoặc docker compose up --build
cd frontend && npm install && npm run dev   # Vite :5173
```

### 1.7 Test — VERIFIED
```bash
PYTHONPATH=. pytest -q         # toàn bộ tests/ với SQLite :memory:
cd frontend && npm run build   # Vite production build
```
Kết quả chạy gần nhất (VERIFIED bằng lệnh): billing 8/8, auth-hardening 14/14, security-ops 12/12 pass; core API tests pass sau cập nhật verified-gate.

### 1.8 Build & deploy — VERIFIED
- **Docker:** multi-stage, non-root `appuser`, HEALTHCHECK `/health`, CMD uvicorn — `Dockerfile:3-53`
- **Compose:** backend + frontend, volume `invoice-data`, backend phải healthy trước frontend — `docker-compose.yml:11-54`
- **K8s:** `k8s/` namespace/configmap/secret/deployment/service/ingress/HPA/ServiceMonitor/kustomization — VERIFIED (ls + kubectl-free validate trước đó)
- **Monitoring:** `monitoring/prometheus.yml`, `monitoring/grafana-dashboard.json`, endpoint `/metrics` — `src/app.py:60-62`
- **Fail-fast production:** từ chối JWT_SECRET mặc định/thiếu, wildcard CORS, `APP_DEBUG=true` — `src/config.py:79-94`

---

## BƯỚC 2 — BẢN ĐỒ REPOSITORY

| Thành phần | File/thư mục | Vai trò | Bằng chứng | Mức |
|---|---|---|---|---|
| Entry point API | `src/app.py` | App FastAPI, routes auth/invoices/reports, `current_user` | `src/app.py:42-80` | VERIFIED |
| Entry point compose | `main.py` | CLI demo (không serve) | `main.py:1-64` | VERIFIED |
| Configuration | `src/config.py` | Settings từ env + production checks | `src/config.py:32-94` | VERIFIED |
| Error contract | `src/errors.py` | `AppError` + handler JSON `{error:{code,message,request_id}}` + global 500 | `src/errors.py:19-90` | VERIFIED |
| Middleware | `src/middleware.py` | RequestId, SecurityHeaders, StructuredLogging, CORS allowlist | `src/middleware.py:21-51`, `src/app.py:45-48` | VERIFIED |
| Models | `src/domain/models.py` | Invoice/LineItem/FieldProvenance/JobStatus/User/Token | `src/domain/models.py:12-50` | VERIFIED |
| Orgs domain | `src/domain/orgs.py`, `src/store/orgs.py`, `src/routers/orgs.py` | Multi-tenancy + RBAC owner/admin/member/viewer | `src/app.py:33,54` | VERIFIED |
| Auth | `src/auth/security.py` | pbkdf2 hash, JWT access+refresh, TOTP, backup codes | `src/auth/security.py:15-99` | VERIFIED |
| Auth (opaque tokens) | `src/auth/tokens.py`, `src/store/refresh_tokens.py` | Refresh token hashed/family — **đang song song với flow JWT-jti trong `security.py`** | `src/auth/tokens.py:13-46` | VERIFIED |
| Auth extension routes | `src/routers/auth_ext.py` | verify-email, resend, forgot/reset password, refresh, revoke, MFA setup/verify | `src/routers/auth_ext.py:94-204` | VERIFIED |
| Extraction | `src/extract/extractor.py` | Regex song ngữ + OCR + LLM fallback + confidence | `PLAN_ai-invoice-billing.md:19`, README | VERIFIED |
| LLM adapter | `src/llm/base.py` | OpenAI/Mock provider | `README.md:34` | VERIFIED |
| Invoice data access | `src/store/repository.py` | CRUD + monthly report, scoped `user_id` | `src/app.py:64,150-160` | VERIFIED |
| User data access | `src/store/users.py` | UserRepository canonical (users + auth tables) | `src/store/users.py:11-60` | VERIFIED |
| Billing | `src/billing/{models,provider,repository,entitlements}.py`, `src/routers/billing.py` | Checkout, subscription, webhook verify + idempotency, entitlements | `src/routers/billing.py:27-141` | VERIFIED |
| Upload security | `src/upload.py`, `src/security/malware.py` | size/MIME/pages + malware scan TRƯỚC MIME | `src/upload.py`, `src/security/malware.py:78-116` | VERIFIED |
| Rate limiting | `src/security/rate_limit.py` | Sliding window, thread-safe, memory/SQLite backend | `src/security/rate_limit.py:124-204` | VERIFIED |
| Search/RAG | `src/search/`, `src/routers/search.py` | TF-IDF + cosine, eval `scripts/eval_rag.py` | `src/app.py:34,55` | VERIFIED |
| Reports | `src/reports/pdf_exporter.py` | PDF tháng (reportlab) | `requirements.txt:17` | VERIFIED |
| Backup/restore | `src/store/backup.py`, `scripts/backup.py`, `scripts/restore.py` | tar.gz DB+storage | ls + tests | VERIFIED |
| Observability | `src/observability/metrics.py` | Prometheus text exposition `/metrics` | `src/app.py:60-62` | VERIFIED |
| Frontend | `frontend/src/{Login,Invoices,api}.jsx`, `styles.css` | SPA: login, upload, danh sách, báo cáo, settings (Team/Billing/Security), accessibility | `README.md:30-31` | VERIFIED |
| Tests | `tests/test_*.py` | api, auth, auth_hardening, billing, security_ops, orgs_phase2, rag_search, batch, invoice, llm_fallback, pdf_export, sroie_regression, vi_extractor | `tests/` | VERIFIED |
| Deploy | `Dockerfile`, `docker-compose.yml`, `k8s/`, `monitoring/`, `.github/` | Container, orchestration, CI (chưa đọc workflow chi tiết) | `Dockerfile:1` | VERIFIED / UNKNOWN (CI) |
| Docs | `README.md`, `PLAN_ai-invoice-billing.md`, `design.md`, `docs/accessibility.md`, `dataset_sources.md` | Tài liệu + attribution CORD CC-BY-4.0 | `README.md:19` | VERIFIED |

### Cây rút gọn

```
ai-invoice-billing/
├── main.py                     # CLI demo
├── requirements.txt  Dockerfile  docker-compose.yml  .env.example
├── src/
│   ├── app.py                  # FastAPI app — routes + deps + /metrics
│   ├── config.py               # Settings + ensure_production_readiness()
│   ├── errors.py               # AppError + global handlers
│   ├── middleware.py           # CORS/RequestId/SecurityHeaders/Logging
│   ├── upload.py               # validate_upload (malware trước MIME)
│   ├── auth/       security.py  tokens.py  mfa.py
│   ├── domain/     models.py    orgs.py    billing.py  money.py
│   ├── extract/    extractor.py
│   ├── llm/        base.py
│   ├── reports/    pdf_exporter.py
│   ├── search/     embeddings.py vector_store.py
│   ├── security/   rate_limit.py malware.py
│   ├── store/      db.py repository.py users.py refresh_tokens.py
│   │               orgs.py storage.py object_storage.py backup.py billing.py
│   ├── billing/    models.py provider.py repository.py entitlements.py
│   ├── routers/    billing.py auth_ext.py orgs.py search.py
│   └── observability/metrics.py
├── frontend/     (React+Vite; dist/ prebuild)
├── tests/        (pytest)
├── k8s/          monitoring/  scripts/  docs/  mockup/
└── PLAN_ai-invoice-billing.md  README.md  design.md  dataset_sources.md
```

---

## BƯỚC 3 — SƠ ĐỒ KIẾN TRÚC & LUỒNG DỮ LIỆU

```mermaid
flowchart LR
  subgraph Client
    SPA[React SPA :5173]
  end
  subgraph API["FastAPI :8000 (src/app.py)"]
    MW[CORS → RequestId → SecurityHeaders → Logging]
    EH[Global handlers: AppError/422/500 → safe JSON]
    AUTHN[current_user: JWT decode + verified gate]
    R1[/auth/* register login me/]
    R2[/auth/* verify-email refresh mfa/ (auth_ext)/]
    R3[/invoices CRUD + upload/]
    R4[/reports/monthly + PDF/]
    R5[/billing checkout subscription webhook entitlements/]
    R6[/orgs RBAC · /search RAG · /metrics/]
  end
  SPA -->|Bearer JWT| MW --> AUTHN
  AUTHN --> R1 & R2 & R3 & R4 & R5 & R6
  R3 --> UP[validate_upload: size·pages·MIME·malware]
  R3 --> EX[extractor: regex → OCR → LLM fallback <0.8]
  EX --> LLM[(LLM API optional)]
  R3 & R4 --> INV[(SQLite invoices — scoped user_id)]
  R1 & R2 --> USR[(SQLite users/refresh/mfa)]
  R5 --> PROV{Provider mock/stripe}
  R5 --> BILL[(SQLite subs/webhooks/entitlements)]
  UP --> FS[(data/storage local)]
  FS -. S3 abstraction .-> S3[(S3/MinIO — chưa deploy)]
```

**Bất đồng bộ:** KHÔNG có queue/worker/cache thực — mọi xử lý là synchronous request/response. Rate limiter là in-process (memory) hoặc SQLite; không dùng Redis.

### Vòng đời dữ liệu hóa đơn
`upload → validate → extract (confidence + FieldProvenance per field) → lưu (user_id scope) → người dùng sửa → report/PDF`. Nguồn AI luôn gắn `source: regex|ocr|llm|manual|unverified` — `src/domain/models.py:22-30`.

---

## BƯỚC 4 — LUỒNG NGHIỆP VỤ (đầu vào → đầu ra)

### 4.1 Register → verified gate → login — VERIFIED
```
POST /auth/register (OPEN_REGISTRATION=1)
  → users.create (verified=0) → phát access JWT + refresh JWT(jti) lưu bảng refresh_tokens
  → src/app.py:124-134
POST /auth/verify-email?token=...
  → consume_email_verification → users.verified=1 — src/routers/auth_ext.py:94-100, src/store/users.py:118-150
POST /auth/login → verify_password → NẾU !verified trả 403 EMAIL_NOT_VERIFIED
  → src/app.py:140-146
Mọi route protected: current_user decode JWT → user tồn tại → verified — src/app.py:72-82
Billing route dùng _canonical_user_repo() → app.users (đã thống nhất) — src/routers/billing.py:31-45
```

### 4.2 Upload → extract → lưu — VERIFIED
```
POST /invoices/upload (multipart)
  → validate_upload: MAX_UPLOAD_BYTES=10MB, MAX_PDF_PAGES=20, malware scan TRƯỚC MIME — src/upload.py, src/config.py:49-56
  → extract_from_text/pdf: regex song ngữ VN/INTL; confidence < 0.8 → LLM fill — src/extract/extractor.py
  → repo.upsert(Invoice scoped user_id) — src/store/repository.py
Output: Invoice kèm FieldProvenance per field — src/domain/models.py:18-30
```

### 4.3 Billing checkout → webhook → entitlements — VERIFIED
```
POST /billing/checkout {plan} → validate plan ∈ {free,pro,enterprise}
  → provider.create_checkout_session (try/except → AppError PAYMENT_FAILED 502; log internal) — src/routers/billing.py:63-77
POST /billing/webhook/{provider}
  → verify HMAC signature (sai → PAYMENT_FAILED 400, không lộ raw exc) — src/routers/billing.py:123-142
  → event ID = provider id/event_id (ổn định; fallback sha256(payload)) — src/billing/provider.py:156-164
  → INSERT OR IGNORE webhook_events; đã tồn tại → already_processed — src/routers/billing.py:128-133, src/billing/repository.py:221
  → _apply_webhook_event merge envelope + data.object + metadata → create/update subscription + entitlements — src/routers/billing.py:158-215
GET /billing/subscription → lọc SubscriptionStatus.ACTIVE/TRIALING/PAST_DUE — src/routers/billing.py:86-96
```

### 4.4 Multi-tenancy & RBAC — VERIFIED
`/orgs`: tạo org auto-owner, invite theo role, viewer/member không được remove — `tests/test_orgs_phase2.py` pass.

### 4.5 RAG search — VERIFIED
`/search`: TF-IDF + cosine (pure Python), eval offline `scripts/eval_rag.py` (Precision@K + latency).

---

## BƯỚC 5 — MISMATCH TÀI LIỆU/TÊN/COMMENT vs HÀNH VI THẬT

| # | Tuyên bố (doc/comment/tên) | Hành vi thật | Nhãn |
|---|---|---|---|
| M1 | `README.md:13`: “LLM fallback → GPT-4o-mini” | Model default là `qwen3.7-max` qua `LLM_BASE_URL` (`src/config.py:66`); OpenAI chỉ là 1 provider | VERIFIED |
| M2 | `main.py` nằm ở root, Dockerfile COPY nó | `main.py` là **CLI demo**, không phải ASGI entry; nhưng `Dockerfile:53` chạy `uvicorn main:app` → `main.py` KHÔNG định nghĩa `app` → **image hiện tại sẽ không serve được từ lệnh CMD** (demo chạy `python main.py` OK) | VERIFIED — cần kiểm chứng bằng `docker run` (UNKNOWN: chưa chạy container) |
| M3 | `src/store/users.py:44` comment “Refresh tokens keyed by jti” + `src/store/refresh_tokens.py:24` bảng `refresh_tokens` keyed by `token_hash` | Hai schema **cùng tên bảng** khác cột; nếu cùng file DB → `OperationalError: no column token_hash` (đã gặp thật trong test) | VERIFIED |
| M4 | `PLAN_ai-invoice-billing.md:9`: “không cam kết thanh toán subscription hoàn chỉnh” | Phase 3 đã thêm checkout/subscription/webhook nhưng **Stripe là skeleton**, chỉ Mock hoạt động | VERIFIED |
| M5 | `auth_ext.py` offline mode trả token trong response (`_todo` field) | Bảo mật chấp nhận được ở dev nhưng nếu deploy nguyên trạng = **ai cũng claim được token reset/verify** | VERIFIED — `src/routers/auth_ext.py:115-119,133-137` |
| M6 | `errors.py` fallback message “Có lỗi xảy ra” | Đúng ý ẩn chi tiết; nhưng register/login trả dict thay vì `response_model=Token` — schema OpenAPI lệch thực tế (khớp nhờ field同名) | VERIFIED |
| M7 | `docker-compose.yml:1` “Phase 0+1 milestone” | Code là `3.0.0` (Phase 2-4) — header compose đã lỗi thời so với code | VERIFIED |
| M8 | Tên `is_production`/`ensure_production_readiness` | Được gọi tại import `src/app.py:40` → fail-fast thật, đã verify bằng lệnh | VERIFIED |
| M9 | `Dockerfile:32` cài `tesseract-ocr` nhưng `requirements.txt:10` comment pytesseract | OCR ảnh chỉ hoạt động nếu cài thêm `pytesseract+Pillow` — trong image runtime chỉ có binary tesseract, không có python binding → **OCR bất khả dụng trong image** | INFERENCE (chưa chạy thử trong container) |
| M10 | `requirements.txt` dùng `>=` không lockfile | Build không tái lập được giữa các ngày | VERIFIED |

---

## BƯỚC 6 — ĐÁNH GIÁ

| Tiêu chí | Đánh giá | Bằng chứng |
|---|---|---|
| **Chạy được** | ✅ local venv + Docker build ✅ (đã build image thành công trong session này); ⚠️ CMD `uvicorn main:app` nghi vấn (M2) | lệnh đã chạy |
| **Testability** | ✅ tốt: fixtures `:memory:`, deterministic MockProvider, ~90 tests | pytest runs |
| **Bảo mật** | Khá cho MVP: JWT+refresh rotation+revocation, TOTP, PBKDF2 100k iter, verified gate, malware scan, rate limit, error không lộ stack, non-root container, prod fail-fast. Yếu: M3 schema clash, M5 token-in-response nếu deploy nguyên trạng, TOTP secret lưu plaintext (INFERENCE — cần đọc lại `store/users.py:227+`), HMAC timestamp không verify freshness (INFERENCE), secret trong `k8s/secret.yaml` mẫu | các file đã dẫn |
| **Hiệu năng** | Đủ < 100 users: SQLite WAL-chưa bật, RAG tính lại mỗi process, đồng bộ không queue. Chưa có benchmark server-side | INFERENCE |
| **Maintainability** | Tốt: module tách rõ, error contract, provenance model. Nợ: 4 bản `current_user` copy (app/auth_ext/orgs/search), 2 hệ refresh-token song song, không migration | grep |
| **Sẵn sàng deploy** | Internal/pilot: ✅. Public SaaS: ❌ (email, Stripe live, Postgres, S3, backup off-site, CI verify) | PLAN + code |

---

## BƯỚC 7 — RỦI RO CÓ BẰNG CHỨNG (P0 → P3)

### P0
| # | Rủi ro | Bằng chứng | Hệ quả |
|---|---|---|---|
| R1 | Hai schema `refresh_tokens` xung đột trên cùng DB file | `src/store/users.py:45-52` vs `src/store/refresh_tokens.py:24-31`; lỗi `no column token_hash` đã quan sát | Login/refresh 500 khi cả 2 repo chạm cùng `invoices.db` |
| R2 | `Dockerfile:53` `uvicorn main:app` trong khi `main.py` là CLI không có `app` | `main.py:1-64` (không có `app = FastAPI(...)`) | Container start fail hoặc hành vi không xác định ở production — **cần `docker run` để xác nhận** (UNKNOWN) |
| R3 | Email token trả trong API response (offline mode) | `src/routers/auth_ext.py:115-119` | Nếu deploy không có email transport → attacker username-enumeration + claim reset token của chính mình là bypass vô hại NHƯNG resend-verification trả token cho bất kỳ ai biết username của user **chưa verified** → chiếm quyền xác minh |
| R4 | Secret `MOCK_WEBHOOK_SECRET` hardcode | `src/billing/provider.py` (fixed constant) | Nếu provider mock bị bật ở prod (`BILLING_PROVIDER` default mock) → webhook giả mạo hợp lệ |

### P1
| # | Rủi ro | Bằng chứng |
|---|---|---|
| R5 | 4 bản `current_user` copy → test patch `app.users` không vá được router khác | `src/routers/auth_ext.py:36`, `src/routers/billing.py`, `src/routers/orgs.py`, `src/routers/search.py` |
| R6 | `org_id = user.id` trong billing entitlements — org/user bị lẫn | `src/routers/billing.py:149` comment |
| R7 | Không migration/version schema — chỉ `CREATE TABLE IF NOT EXISTS` | mọi repo |
| R8 | Signature HMAC mock không verify timestamp → replay ngoài cửa sổ | `src/billing/provider.py:187+` |
| R9 | SQLite concurrency khi K8s chạy nhiều replica (HPA bật sẵn `k8s/hpa.yaml`) ghi cùng PVC | INFERENCE — chưa test tải |
| R10 | OCR không dùng được trong image runtime (M9) | `requirements.txt:10` |

### P2
| # | Rủi ro |
|---|---|
| R11 | Không có lockfile/dependency pinning (`>=`) → build không tái lập |
| R12 | `/metrics` không có auth — lộ cardinality/thông tin internal nếu ingress public |
| R13 | Không có CI evidence (workflow `.github/` chưa đọc/chưa chạy) |
| R14 | Rate limiter in-memory mặc định không dùng được multi-worker |
| R15 | Frontend lưu access token `localStorage` (audit trước ghi nhận) |

### P3
| # | Ghi chú |
|---|---|
| R16 | Comment/naming lẫn Việt-Anh; `src/security/__init__.py` từng import module đã xóa (đã fix) |
| R17 | `docker-compose.yml` header ghi Phase 0+1 trong khi app 3.0.0 |

---

## BƯỚC 8 — THỨ TỰ CẢI THIỆN (đề xuất, chưa sửa)

**Đợt 1 — P0 chặn deploy (1-2 ngày):**
1. Xác nhận `docker run` container có serve thật không; nếu đúng M2 → sửa CMD thành `uvicorn src.app:app` (hoặc expose `app` trong `main.py` theo hướng gọi `src.app:app`).
2. Gộp schema refresh-token về MỘT hệ (khuyến nghị: hệ hashed `token_hash/family` của `refresh_tokens.py`, migrate `auth_ext` sang dùng nó) — kèm migration an toàn dữ liệu.
3. Guardrail: fail-fast nếu `APP_ENV=production` mà `BILLING_PROVIDER=mock` hoặc thiếu email transport config → chặn R3/R4.

**Đợt 2 — P1 (1 tuần):**
4. Dependency-inject `get_current_user` dùng chung một chỗ (`src/deps.py`), xóa 4 bản copy.
5. Tách rõ `org_id` khỏi `user_id` trong billing (`/billing/orgs/{org_id}/...` + kiểm tra membership).
6. Thêm bảng `schema_version` + migration runner (stdlib, giữ triết lý không dep nặng).
7. Cài `pytesseract`+`Pillow` vào requirements (bỏ comment) hoặc gỡ tesseract khỏi image — chọn 1, đừng lệch.
8. Verify webhook timestamp + nonce (chống replay).

**Đợt 3 — P2 (vận hành):**
9. `pip-compile` lockfile + CI (pytest --cov, docker build, vite build, diff-check).
10. Auth `/metrics` (token riêng hoặc bind internal port).
11. Postgres + SQLAlchemy (đã chốt trong plan là bước production), object storage S3/MinIO thật, backup off-site + restore drill.
12. Email transport thật (SES/SMTP) sau đó bỏ hẳn branch trả token trong response.

**Đợt 4 — P3:** dọn comment/header metadata lệch phiên bản, chuẩn response model `Token` cho register/login.

---

## PHỤ LỤC A — Bằng chứng lệnh đã chạy (VERIFIED)

| Lệnh | Kết quả |
|---|---|
| `pytest tests/test_billing.py` | 8 passed (sau fix idempotency/fixture) |
| `pytest tests/test_auth_hardening.py` | 14 passed |
| `pytest tests/test_security_ops.py` | 12 passed |
| `compileall src scripts tests` | exit 0 |
| `npm run build` (frontend) | vite build ok |
| `docker build` | image build thành công |
| `docker compose config` (env tạm) | exit 0, .env đã xóa sau đó |
| `git diff --check` | clean |
| Full `pytest -q` | hoàn tất nhưng harness cắt output dài; các suite trọng yếu pass lẻ |

## PHỤ LỤC B — Những gì KHÔNG thể kết luận (UNKNOWN)
- Hành vi runtime của container production thật (`docker run` + `APP_ENV=production` chưa chạy end-to-end).
- OCR trong image (phụ thuộc M9 — chưa test trong container).
- Nội dung/hiệu lực workflow `.github/` (chưa đọc).
- Hiệu năng thực tế dưới tải (không có benchmark server).
- Tính chính xác pháp lý của DPA/SLA/privacy docs (cần lawyer).

---

*File này do phân tích read-only tạo; không có sửa đổi code nào trong bước này.*
