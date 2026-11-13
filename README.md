# Quanh Ta — Khám phá quán ăn theo mức chi tiêu

> Ghi một lần bạn đã chi bao nhiêu ở một quán → biết "mức chi quen thuộc" của mình → tìm quán quanh đây khớp mức đó.

## Vấn đề

Bạn không nhớ quán nào "vừa túi tiền". Giá trên menu thì mỗi nơi một kiểu, review thì
quảng cáo. Nhưng **bạn nhớ mình đã trả bao nhiêu** — và đó là tín hiệu đáng tin hơn.

## Cách hoạt động

1. **Ghi** — trả tiền ở quán: nhập số tiền, số người, địa điểm (hoặc quét QR chuyển khoản).
2. **Hồ sơ mức chi** — hệ thống gom các lần chi theo loại địa điểm (quán cà phê, quán ăn,
   trà sữa…) và tính khoảng p25–p75 của **mức chi trên mỗi người**.
3. **Khám phá** — quán quanh bạn được chấm điểm theo mức chi, khoảng cách, loại quán và
   lịch sử ghé. Quán khớp khoảng chi được gắn nhãn **"Phù hợp mức chi tiêu"**.

### Quy ước dữ liệu — đọc trước khi dùng số

Số tiền chuyển khoản **không phải giá món**. Một lần trả 250.000đ có thể là 2 người,
5 người hay cả nhóm. Vì vậy mọi con số trong app đều được gọi là
**"mức chi ghi nhận tại địa điểm"** (chia cho số người), không bao giờ là
"giá trung bình mỗi món" hay "giá niêm yết".

Hệ quả trực tiếp trong code:

- `party_size` là trường bắt buộc của mỗi lần chi — nó là thứ làm con số trên trung thực.
- `spend_per_person` là **thuộc tính dẫn xuất** (`amount / party_size`), không lưu vào DB.
- Quán **chưa có lần chi nào** (ví dụ gợi ý từ OpenStreetMap) không được chấm điểm giá và
  không bao giờ nhận nhãn "Phù hợp mức chi tiêu" — chúng nằm ở nhóm "chưa rõ giá".
- Dưới 3 lần chi cho một loại quán thì khoảng chi là **ước lượng ban đầu** (`is_default`),
  không phải số đo của bạn.

Chấm điểm: `0.35 × mức chi + 0.30 × khoảng cách + 0.20 × loại quán + 0.15 × lịch sử`.
Trọng số là giả thuyết ban đầu, **chưa được hiệu chỉnh bằng dữ liệu thật** — đừng coi
điểm số là kết luận khoa học.

Không có AI/LLM trong luồng này. Chỉ lọc, xếp hạng và thống kê.

## Tính năng (MVP)

- Ghi lần chi: số tiền, số người, loại, địa điểm, ghi chú; quét QR chuyển khoản để điền số tiền.
- Danh sách lịch sử chi (lọc theo khoảng ngày), xoá lần chi của chính mình.
- Bản đồ các quán đã ghi (Leaflet).
- Tìm quán theo tên / loại / khoảng mức chi / bán kính.
- Gợi ý quán quanh đây theo hồ sơ chi tiêu của bạn.
- Gợi ý địa điểm từ OSM khi chưa có trong dữ liệu (đánh dấu rõ là chưa có giá).
- Auth JWT, xác minh email qua SMTP (tuỳ chọn), Google OAuth (tuỳ chọn).

**Chưa làm (cố ý):** chia sẻ cộng đồng, nhập dữ liệu TikTok, chụp hoá đơn/OCR, LLM,
tổ chức/API key/push/report.

## Kiến trúc

```
├── src/
│   ├── app.py               # CORS, middleware, router, init DB
│   ├── config.py            # settings từ env + fail-fast production
│   ├── models.py            # User, RefreshToken, EmailVerification, PasswordReset, Place, Spend
│   ├── discovery.py         # bands + scoring (thuần, không I/O)
│   ├── geo.py               # haversine, chuẩn hoá khoảng cách
│   ├── places_osm.py        # gợi ý địa điểm từ OpenStreetMap (timeout 6s)
│   ├── qr.py                # đọc số tiền/tên quán từ text QR (JSON, URL, số trần, VietQR)
│   ├── api/deps.py          # current_user + get_db dùng chung
│   ├── routers/             # auth_ext, google_oauth, places, spends, discover
│   └── store/sql_repository.py  # SQLite: Database, UserRepository, PlaceRepository, SpendRepository
├── frontend/src/            # React 18 + Vite: Discover.jsx, History.jsx, MapView.jsx
├── tests/                   # pytest: auth, places, spends, discovery, QR, schema
└── data/                    # dữ liệu thật (không commit); DB mặc định data/spends.db
```

Lưu ý: `invoices.db` ở root là **dữ liệu cũ của sản phẩm trước**, không còn được app đọc
hay sửa. Không xoá nếu bạn còn muốn giữ.

## Chạy local

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # đặt OPEN_REGISTRATION=1 để tự đăng ký
python -m uvicorn src.app:app --reload --port 8000
```

Swagger: http://localhost:8000/docs · health: `/health` · readiness: `/ready`.

DB mặc định `data/spends.db`. Muốn chạy thử với DB riêng:

```bash
DATABASE_PATH=/tmp/spends-test.db python -m uvicorn src.app:app --port 8005
```

### Frontend

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxy /auth /places /spends /discover -> :8005
```

Đổi cổng backend thì sửa `API_TARGET` trong `frontend/vite.config.js`.

### Docker Compose

```bash
cp .env.example .env
# Sửa JWT_SECRET (bắt buộc ở production) + CORS_ORIGINS + APP_URL.
# SITE_ADDRESS=:80 -> HTTP thuần; SITE_ADDRESS=quanhta.example.com -> Caddy tự lấy cert.
docker compose up --build
```

Backend http://localhost:8000, frontend (Caddy) http://localhost. SQLite nằm trong
volume `quanhta-data` (`/app/data/spends.db`).

Trên VPS mới: `sudo ./scripts/deploy.sh yourdomain.com`.

Frontend gọi API bằng path tương đối nên chỉ cần cùng origin — không cần CORS khi chạy
sau Caddy. Caddy tin `X-Forwarded-For` từ IP tĩnh `172.28.0.10` (xem
`RATE_LIMIT_TRUSTED_PROXIES` trong `.env.example`); đổi subnet trong `docker-compose.yml`
thì phải đổi cả hai nơi.

## API

Tất cả route dưới đây (trừ `/health`, `/ready`, `/auth/register`, `/auth/login`) cần
`Authorization: Bearer <token>`.

| Method | Endpoint | Mô tả |
|---|---|---|
| POST | `/auth/register` | Đăng ký (email xác minh nếu cấu hình SMTP) |
| POST | `/auth/login` | Lấy access + refresh token |
| POST | `/auth/verify-email` | Xác minh bằng token |
| POST | `/auth/resend-verification` | Gửi lại link xác minh |
| POST | `/auth/forgot-password` / `/auth/reset-password` | Đặt lại mật khẩu |
| POST | `/auth/refresh` / `/auth/revoke` | Refresh / thu hồi token |
| GET | `/places?q=&category=&lat=&lon=&radius_m=&min_price=&max_price=` | Tìm địa điểm; lọc giá theo **mức chi của bạn** |
| POST | `/places` | Thêm địa điểm (cùng tên + ≤50 m → gộp) |
| GET | `/places/nearby?lat=&lon=&radius_m=` | Gợi ý OSM, luôn `priced: false` |
| POST | `/spends` | Ghi lần chi (`place_id`, `amount`, `party_size`, `occurred_at`, `note`) |
| GET | `/spends?start=&end=` | Lịch sử chi của bạn |
| DELETE | `/spends/{id}` | Xoá lần chi của mình |
| POST | `/spends/parse-qr` | Text QR → `{amount, party_size, merchant_name}`; nhận JSON / URL / số trần / VietQR-EMVCo; không đọc được → `amount: null` |
| GET | `/discover/bands` | Khoảng chi p25/p50/p75 theo loại + `sample_size` / `is_default` |
| GET | `/discover?lat=&lon=&radius_km=&category=&q=&limit=` | Danh sách quán đã chấm điểm + nhóm `unpriced` |

Mọi response chứa số tiền đều kèm `spend_label: "mức chi ghi nhận tại địa điểm"`.

## Tests

```bash
pytest -q
```

Bao phủ: auth + hardening, Google OAuth, cách ly dữ liệu giữa user, gộp địa điểm theo
tên + khoảng cách, ghi/xoá lần chi, `spend_per_person`, parse QR (JSON/URL/số trần/VietQR/rác),
bands mặc định khi thiếu mẫu, thứ tự ưu tiên của việc khớp mức chi so với khoảng cách,
quán chưa có giá không được chấm điểm, và `test_schema_migration.py` khoá hành vi
khởi tạo DB.

## Giới hạn đã biết

- Trọng số chấm điểm chưa hiệu chỉnh; chưa có A/B hay dữ liệu người dùng thật để đo.
- Khoảng chi theo loại quán cần ≥3 lần chi mới là số đo của bạn, ít hơn thì dùng mặc định.
- Một lần chi lớn cho nhiều người vẫn làm lệch p75 nếu bạn quên nhập `party_size`.
- Gợi ý OSM không có giá — app không suy đoán giá cho chúng.
- SQLite là single-writer: chỉ chạy 1 backend replica.
