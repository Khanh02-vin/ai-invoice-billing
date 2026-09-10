# PLAN_ai-invoice-billing.md

## 1. Mục tiêu sản phẩm

Đóng gói `ai-invoice-billing` thành sản phẩm độc lập cho hộ kinh doanh và doanh nghiệp nhỏ:

> Upload hóa đơn → AI/OCR trích xuất dữ liệu → người dùng kiểm tra/sửa → lưu hồ sơ → báo cáo và xuất dữ liệu.

Phiên bản đầu **không** cam kết là hệ thống thanh toán subscription hoàn chỉnh. `billing` trong sản phẩm ban đầu là quản lý hóa đơn nghiệp vụ; thanh toán SaaS chỉ được thêm ở phase monetization.

## 2. Baseline đã xác nhận

### Đã có

- FastAPI backend và React/Vite frontend.
- JWT register/login/current-user.
- SQLite repository.
- Upload đơn/bulk và invoice CRUD.
- Regex/OCR/LLM extraction fallback.
- Báo cáo tháng JSON/PDF.
- SROIE fixtures, regression/benchmark tests.
- MIT license; dataset attribution nằm trong `dataset_sources.md`.

### Chưa đủ để bán production

- Chưa có organization/workspace và team roles; isolation hiện chủ yếu theo user.
- Chưa có Stripe/Paddle/payment checkout, subscription, webhook hoặc entitlements.
- JWT có fallback secret dev, token dài hạn và chưa có refresh/revocation đầy đủ.
- CORS đang quá rộng; thiếu rate limit, email verification, reset password và MFA.
- Upload thiếu giới hạn dung lượng/type/page, malware scan và object storage bền vững.
- Schema invoice còn thiếu line items, subtotal, tax rate, tax IDs, provenance và correction history.
- Tiền tệ dùng `float`/logic báo cáo cố định; chưa có reconciliation chặt.
- SQLite chưa có migration, backup/restore, retention và concurrency strategy.
- Frontend còn shell cho settings/dashboard/team/billing, thiếu error/empty state và accessibility.
- Chưa có CI, Docker, observability, privacy/DPA/SLA.

## 3. Nguyên tắc thực thi

1. Chọn một wedge đầu tiên: **trích xuất hóa đơn/receipt Việt Nam và tiếng Anh**.
2. Không quảng cáo benchmark hiện tại thành SLA sản phẩm.
3. Mọi field AI phải có confidence và evidence/provenance để người dùng kiểm tra.
4. Không gửi PII tới LLM bên ngoài nếu chưa có opt-in, redaction và cấu hình provider rõ ràng.
5. Mọi thay đổi dữ liệu phải thuộc một organization và được audit.
6. Dùng `Decimal`, UTC-aware datetime và schema versioned.
7. Không commit secret, không dùng default JWT secret ở production.
8. Ưu tiên workflow upload → review → finalize trước khi thêm subscription billing.

## 4. Kiến trúc mục tiêu

```text
React/Vite
   ↓ JWT/refresh + API client
FastAPI API
   ├── Auth / Organizations / Roles
   ├── Upload + Job status
   ├── Invoice extraction adapter
   ├── Review/version/finalize
   ├── Reports/export
   └── Usage/entitlements/billing
        ↓
PostgreSQL (metadata, users, audit, usage)
Object storage (originals, derived files)
Worker/queue (OCR/LLM/extraction)
Redis (queue/cache/rate limit)
Payment provider (phase 4)
```

SQLite vẫn được giữ cho local/demo, nhưng phải có repository abstraction và migration path sang PostgreSQL.

## 5. Kế hoạch theo phase

### Phase 0 — Production foundation và baseline đáng tin cậy

**Thời lượng:** 1–2 tuần.

#### Công việc

- Tạo settings theo environment; fail fast khi production thiếu secret.
- Pin dependency và tạo lock/CI matrix.
- Thêm Docker/dev compose cho API, frontend và database.
- Thêm Alembic hoặc migration system; seed chỉ cho development.
- Chuẩn hóa `Decimal`, currency code và UTC datetime.
- CORS allowlist; security headers; request ID; structured logging.
- Giới hạn upload theo bytes, MIME sniffing bằng content chứ không chỉ extension, số trang và thời gian xử lý.
- Tạo error contract JSON ổn định; không trả stack trace/PII.
- Thêm malware/virus scan adapter hoặc quarantine hook.
- Health/readiness endpoint kiểm tra DB, storage và provider.
- Bổ sung test cho auth, upload abuse, CORS, config và money arithmetic.

#### Acceptance criteria

- Clean install chạy được API và frontend.
- CI chạy test, lint/type check và không cần secret thật.
- Production config từ chối `JWT_SECRET` mặc định hoặc thiếu.
- File quá lớn, sai MIME, file hỏng và file độc hại mẫu bị từ chối.
- Không có CORS wildcard trong production.
- Health/readiness phân biệt được process sống và dependency sẵn sàng.
- Có backup/restore smoke test cho database.

### Phase 1 — Invoice workflow có thể bán được

**Thời lượng:** 2–4 tuần.

#### Công việc

- Thiết kế schema versioned:
  - supplier/vendor
  - buyer/customer
  - invoice number
  - issue/due date
  - currency
  - subtotal
  - tax rate/tax amount
  - discount
  - total
  - supplier/customer tax ID
  - payment status
  - line items
- Mỗi field lưu `value`, `confidence`, `source`, `page`, `bbox/text_span` nếu có.
- Tách upload khỏi xử lý bằng job model: `PENDING`, `PROCESSING`, `REVIEW`, `FINALIZED`, `FAILED`.
- Lưu file gốc và artifact qua object-storage interface; không phụ thuộc temp path.
- Idempotency key và duplicate fingerprint rõ ràng; không silently overwrite.
- Review UI cho sửa từng field, xem ảnh/trang bằng chứng và lưu version.
- Pagination, filter, date range, vendor/status search.
- Export CSV/JSON/PDF; báo cáo dùng Decimal và currency-aware aggregation.
- Provider adapters cho regex, OCR và LLM; timeout/retry/cost cap.
- Privacy toggle: local/remote LLM; redaction trước khi gửi provider.

#### Acceptance criteria

- Upload → job status → extraction → review → finalize chạy end-to-end.
- Mọi field hiển thị evidence hoặc ghi rõ `unverified`.
- Không thể đọc/sửa invoice của user/organization khác.
- Duplicate cùng fingerprint có kết quả deterministic.
- Tổng tiền reconcile được theo line items, tax và discount trong tolerance công bố.
- Có test held-out tối thiểu 100 hóa đơn được gán nhãn; đặt ngưỡng riêng cho field quan trọng.
- Không field nào được coi là đúng chỉ vì model trả confidence cao.

### Phase 2 — Multi-tenant, quyền và bảo mật tài khoản

**Thời lượng:** 2–3 tuần.

#### Công việc

- Thêm `organizations`, `memberships`, `roles`, `invitations`.
- Roles tối thiểu: `owner`, `admin`, `member`, `viewer`.
- Mọi query dùng organization scope; cấm dùng ID đoán được để bypass.
- Refresh token rotation, revoke session, logout-all.
- Email verification, password reset, password policy; MFA sau pilot.
- Rate limit login/upload/export; audit log cho action nhạy cảm.
- Chuẩn hóa user ID ngẫu nhiên/UUID; username/email normalize.
- Chuyển token khỏi localStorage nếu phù hợp deployment; thêm CSRF nếu cookie.

#### Acceptance criteria

- Tenant isolation test bao phủ list/get/update/delete/export/report.
- Role matrix test pass cho mọi endpoint.
- Token bị revoke không dùng lại được.
- Reset password và invitation có expiry/one-time use.
- Audit log chứa actor, organization, action, target, time và request ID.
- Abuse test không cho phép brute-force login/upload.

### Phase 3 — Usage và SaaS monetization

**Thời lượng:** 2–4 tuần.

#### Công việc

- Chọn provider (Stripe/Paddle hoặc provider phù hợp thị trường) sau khi xác nhận pháp lý.
- Thiết kế plans: Free trial, Starter, Business; quota theo pages/invoices/LLM calls/storage.
- Usage ledger append-only; không tính quota từ số đếm frontend.
- Entitlements server-side; grace period/overage rõ ràng.
- Checkout, customer portal, signed webhook, idempotent event handling.
- Billing UI: plan, usage, next renewal, invoices/receipts, cancel.
- Cảnh báo quota và chặn đúng khi vượt quota.

#### Acceptance criteria

- Sandbox checkout → subscription → entitlement cập nhật đúng.
- Webhook replay không tạo double charge/double entitlement.
- Usage ledger reconcile được với job thực tế.
- User không bypass quota bằng retry hoặc nhiều endpoint.
- Cancellation, failed payment, trial expiry có trạng thái xác định.
- Giá và giới hạn hiển thị rõ trước khi upload.

### Phase 4 — Compliance, operations và pilot

**Thời lượng:** 2–4 tuần, sau đó duy trì liên tục.

#### Công việc

- Encryption at rest/in transit; secret manager/KMS.
- Retention policy, delete/export account và purge object/index copies.
- DPA/privacy/terms/security page; dataset/model license review.
- Backup schedule, restore drill, RPO/RTO.
- Metrics: upload success, extraction latency, correction rate, provider cost, queue depth, error rate.
- Alerting, admin support tools, dead-letter/replay job.
- Pilot 3–5 khách hàng cùng một wedge; feedback/correction loop.
- Thay benchmark copyrighted/controlled bằng evaluation set được cấp phép/consented.

#### Acceptance criteria

- Xóa một tenant xóa DB/object/index/audit data theo policy.
- Restore drill đạt RPO/RTO đã công bố.
- PII redaction/provider opt-out được test.
- Có dashboard và alert cho lỗi extraction/queue/provider.
- Pilot có metric accuracy, time saved, correction rate và support burden.
- Không công bố SLA cao hơn kết quả test held-out.

### Phase 5 — Differentiation sau product-market fit

**Không làm trước khi Phase 1–4 ổn định.**

- Email ingestion và vendor inbox.
- QuickBooks/Xero/e-invoice integration.
- Approval workflow, payment reminder.
- Vendor analytics và duplicate/fraud signals.
- Public API keys, webhooks, batch export.
- Custom schema/template theo khách hàng.

## 6. Phạm vi MVP thu tiền đầu tiên

MVP nên chỉ có:

1. Đăng ký/đăng nhập.
2. Tạo organization.
3. Upload PDF/JPG/PNG hóa đơn.
4. Trích xuất 10–15 field cốt lõi.
5. Hiển thị evidence/confidence.
6. Người dùng sửa và finalize.
7. Tìm kiếm/lọc và export CSV/JSON.
8. Giới hạn theo số hóa đơn/tháng.
9. Retention và xóa dữ liệu rõ ràng.

Chưa đưa vào MVP:

- Payment automation.
- Tự động khai thuế.
- Kế toán kép.
- OCR mọi loại tài liệu.
- Cam kết độ chính xác pháp lý.
- LLM không kiểm soát để xử lý PII.

## 7. Rủi ro và cách giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| OCR/LLM trích xuất sai | Evidence, review bắt buộc, field-level metrics, không auto-finalize |
| Lộ PII qua provider | Local mode, redaction, opt-in, DPA, audit |
| Sai số tiền/tax | Decimal, reconciliation, currency-aware rules, human review |
| Cross-tenant access | Organization scope ở repository + integration tests |
| Mất dữ liệu SQLite | Migration, backup, object storage, PostgreSQL path |
| Chi phí LLM vượt kiểm soát | Quota, cost ledger, timeout, provider budget |
| Nghĩa vụ license dataset/model | `dataset_sources.md`, license inventory, thay evaluation data không phù hợp |
| Bán quá sớm | Pilot giới hạn, không quảng cáo SLA chưa được kiểm chứng |

## 8. Definition of Done cho sản phẩm độc lập

- Có clean deployment và rollback.
- Có tenant isolation và audit.
- Upload/extraction/review/finalize/export chạy E2E.
- Có provenance cho field quan trọng.
- Có quota/usage ledger và privacy controls.
- Có backup/restore, logging, metrics và alerting.
- Có evaluation set được phép dùng thương mại.
- Có pricing, terms, privacy và support process.
- Có pilot metrics trước khi mở rộng tính năng.
