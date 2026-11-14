# Backend Rust/Axum — drop-in thay thế FastAPI

Khung backend rewrite bằng **Rust (Axum + SQLx)**, đọc trực tiếp file SQLite WAL
hiện có của nhánh exp2, **không migrate dữ liệu**. Mục tiêu (đề xuất chuyển đổi
tech stack): khoá lỗi dữ liệu lúc biên dịch, binary tĩnh, RAM ~vài MB.

## Trạng thái port

| Nhóm | Endpoint | Trạng thái |
|---|---|---|
| Health | `/health`, `/ready` | ✅ port, parity 1:1 |
| Auth | `/auth/register`, `/auth/login`, `/auth/me` | ✅ port, parity 1:1 |
| Spends | `POST/GET/DELETE /spends` | ✅ port, parity 1:1 |
| Spends | `POST /spends/parse-qr` (EMVCo TLV, VietQR) | ✅ port, parity 1:1 trên vector Python |
| Drafts | `POST/GET /spends/drafts`, `DELETE /spends/drafts/{id}` | ✅ port, parity 1:1 |
| Draft confirm | `POST /spends/drafts/{id}/confirm` (tìm/tạo place, ≤50 m là cùng quán) | ✅ port, parity 1:1 |
| Places | `GET /places` (search + stats + khoảng cách + lọc giá), `POST /places` | ✅ port, parity 1:1 |
| Geo | `geo.normalize_name` (NFD bỏ dấu, đ→d), `haversine_km`, percentile | ✅ port, vector Python |
| Places | `GET /places/nearby` (gợi ý OSM/Nominatim — reqwest + rustls, timeout 4s, lỗi mạng → rỗng) | ✅ port, parity shape + logic parse (vector mock httpx) |
| Discover | `GET /discover/bands`, `GET /discover` (scoring 0.35/0.30/0.20/0.15, unpriced riêng) | ✅ port, parity 1:1 |
| Auth_ext | `/auth/refresh` (rotation + thu hồi), `/auth/revoke`, `/auth/verify-email` (+`/confirm` HTML single-use), `/auth/resend-verification`, `/auth/forgot-password`, `/auth/reset-password` (đổi mật khẩu + thu hồi refresh) | ✅ port, parity 1:1 |
| Rate limit | sliding window in-memory trên register (3:60)/login (5:60)/resend (3:300), 429 + `Retry-After`/`X-RateLimit-*` | ✅ port, parity 1:1 (sequence + headers) |
| SMTP | `mail.send_mail` (lettre): 465 → implicit TLS, còn lại STARTTLS best-effort → plain như Python | ✅ port (lỗi mạng → False; parity chạy ở chế độ chưa cấu hình SMTP → 503 fail-closed) |
| Google OAuth | `/auth/google/status`, `/start`, `/link`(+`/link/start`), `/callback` (JWKS RS256), `/exchange` (state→token 60s single-use), `/unlink`(+`/link`) với guard LAST_LOGIN_METHOD | ✅ port (identity exchange cần Google thật — parity chạy ở chế độ chưa cấu hình: 503/400/404/422 1:1) |
| Rate limit backend SQLite | `RATE_LIMIT_BACKEND=sqlite` (persistence giữa worker) | ⏳ mặc định Python là memory — port sau nếu cần |

## Tương thích chéo Python ↔ Rust (đã verify bằng test)

- **PBKDF2-sha256 100k vòng, base64(salt16+dk32)**: hash do Python sinh, Rust
  verify được và ngược lại (`security.rs` có vector cứng lấy từ DB thật).
- **JWT HS256 `{"sub","exp"}` + refresh `{"sub","jti","type"}`**: token do PyJWT
  tạo decode được ở Rust (`jsonwebtoken`) và ngược lại.
- **Schema SQLite**: `db.rs` chạy `CREATE TABLE/INDEX IF NOT EXISTS` với DDL lấy
  nguyên văn từ `sqlite_master` (SQLModel sinh) — idempotent, không đụng dữ liệu.
- **Datetime**: storage `YYYY-MM-DD HH:MM:SS.ffffff` (naive); input có offset →
  quy đổi UTC khi lưu như SQLAlchemy bind; input naive → giữ giờ wall; JSON
  response luôn có đuôi `+00:00` (SQLModel đọc về gắn UTC mù quáng — đã probe).
- **Error contract**: `{"error":{"code","message","request_id","details"?}}`
  khớp `errors.py`, gồm 422 `details.fields` dạng `[["body","amount"]]` và 429
  `Retry-After`/`X-RateLimit-*` header.
- **Thứ tự FastAPI dependency**: body validation → rate limit → handler logic
  (body sai → 422 kể cả khi đã hết hạn rate limit — parity đã verify).

## Chạy

```bash
cargo build --release
DATABASE_PATH=data/spends.db ./target/release/quanh-ta-backend   # mặc định cổng 8000
```

Biến môi trường giống bản Python: `DATABASE_PATH`, `JWT_SECRET`,
`JWT_EXPIRY_DAYS`, `OPEN_REGISTRATION`, `AUTO_VERIFY_EMAIL`, `CORS_ORIGINS`,
`APP_ENV` (production fail-fast nếu thiếu secret riêng / CORS wildcard),
`PORT` (mặc định 8000).

## Compile-time SQL check

Toàn bộ query tĩnh dùng `sqlx::query!`/`query_as!` — sqlx kết nối DB lúc build
để kiểm tra cú pháp + kiểu với schema thật (sai SQL → **không biên dịch được**).
Config trong `backend-rs/.env` (bị gitignore):

```
DATABASE_URL=sqlite:///đường/dẫn/tuyệt/đối/data/spends.db
```

Nếu clone mới chưa có DB dev, tạo schema trước:

```bash
.venv/bin/python -c "from src.store.sql_repository import init_database; init_database('data/spends.db')"
```

Query động (WHERE thay đổi của `GET /spends`) dùng runtime query — vẫn
parameterized 100%, không ghép chuỗi.

## Kiểm thử

```bash
cargo test          # 44 test: unit (QR/geo/discovery/osm/rate-limit vectors Python,
                    # PBKDF2/JWT chéo, datetime) + 13 API oneshot (Google OAuth 307/503/
                    # LAST_LOGIN_METHOD, auth_ext 429/rotation/reset/HTML confirm,
                    # spends/drafts/places/discover)
```

Parity live (boot cả FastAPI và Axum trên 2 DB sạch, cùng chuỗi request, diff
JSON sau khi scrub `request_id`/token/id ngẫu nhiên — **73/73 PASS**, gồm cả
dùng token Python gọi API Rust và ngược lại, dedup place qua confirm, lọc giá
và radius theo khoảng cách, scoring discover theo band thật, và nearby gọi
Nominatim thật trên cả 2 server so shape).

## Lệch hành vi có chủ đích (khác Python)

1. **`start`/`end` naive** (vd `start=2026-11-01`): Python **500** (SQLModel đòi
   tz-aware); Rust chuẩn hoá về giờ wall rồi lọc bình thường. Frontend thật
   luôn gửi `toISOString()` (có `Z`) nên bug Python là tiềm ẩn.
2. **`merchant_name` giá trị JSON không phải scalar** (object/array): Python trả
   `str(repr)`; Rust trả `null`. QR thật không có dạng này.
3. **`lower(email)`**: dùng `to_lowercase` thay `casefold` — chỉ khác nhau ở
   ký tự hiếm (ß, Đ giữ nguyên), đủ cho email thực tế.
4. **`verification_sent`** luôn `false` cho đến khi port auth_ext + SMTP.

## Số liệu (đo trên máy dev này)

| | FastAPI + Uvicorn | Axum (release) |
|---|---|---|
| RSS khi đang serve | ~90 MB | **4.0 MB** |
| Artifact | venv + 5 package | binary tĩnh **12 MB** (gồm reqwest + lettre) |

## Deploy (docker compose)

- Service `backend-rs` build từ `backend-rs/Dockerfile` (multi-stage: cargo
  build với `fixtures/schema.db` cho sqlx macro → runtime debian-slim + binary).
- Hai backend chạy **song song trên cùng SQLite volume** (WAL multi-process,
  busy_timeout 5s cả hai phía): Python `:8000`, Rust `:8001` (host).
- Caddy mặc định proxy API vào **backend-rs**. Rollback về Python:

  ```bash
  # .env
  BACKEND_UPSTREAM=backend:8000
  docker compose up -d frontend
  ```

- So sánh hai backend sau khi chạy:

  ```bash
  scripts/compare_backends.sh http://localhost:8000 http://localhost:8001
  ```

- `/docs` `/redoc` `/openapi.json` là Swagger của FastAPI — không có trên Rust
  (trả 404 JSON cùng contract). Còn lại mọi endpoint đã port 1:1.
- Build-time SQL check: Docker build tạo `fixtures/schema.db` từ
  `fixtures/schema.sql` (DDL dump từ sqlite_master — giữ đồng bộ với
  `db.rs`; câu tái tạo nằm ở header file).

## Bước tiếp theo (thứ tự đề xuất)

1. Chạy song song Python ↔ Rust trên môi trường thật (parity đủ **100% endpoint**
   ở chế độ không cấu hình dịch vụ ngoài; OAuth identity cần Google thật).
2. Tuỳ chọn: rate limit backend SQLite nếu chạy nhiều worker.
