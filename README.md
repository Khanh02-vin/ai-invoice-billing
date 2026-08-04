# Invoice & Billing System

> Tự động hóa quản lý hóa đơn: upload → trích xuất → lưu trữ → báo cáo.

## Tính năng

- 📄 **Upload hóa đơn** — PDF, ảnh scan (OCR), hoặc text
- 🇻🇳 **Hóa đơn Việt Nam** — GTGT điện tử (Số hóa đơn, Người bán, Tổng cộng, Thuế GTGT)
- 🌍 **Hóa đơn quốc tế** — tiếng Anh (Invoice No, Vendor, Total, Tax)
- 🔍 **Trích xuất thông minh** — số hóa đơn, nhà cung cấp, ngày, tổng, thuế, chiết khấu, tiền tệ
- 🧮 **Nhiều mức thuế** — cộng dồn 10% + 8%...
- 🏷️ **Chiết khấu** — trích xuất riêng, không nhầm với tổng
- 🤖 **LLM fallback** — regex đọc thiếu (confidence < 0.8) → GPT-4o-mini trích xuất lấp chỗ
- 🔒 **Auth JWT + đa người dùng** — mỗi user chỉ thấy hóa đơn của mình
- 💾 **Lưu trữ SQLite** — persistent, không cần database server
- 📊 **Báo cáo theo tháng** — tổng doanh thu, thuế, đã/chưa thanh toán
- ⚛️ **UI React** — SPA hiện đại, build bằng Vite
- 🎨 **Design system** — tokens tập trung trong `design.md` + `frontend/src/styles.css` (`:root`), dark theme
- 🖼️ **Mockup tham khảo** — `mockup/invoice-dashboard.html` (generate bằng open-design, model qwen3-coder-next)

## Giao diện

| Dashboard | Báo cáo | Cài đặt |
|---|---|---|
| ![Dashboard](mockup/ui-dashboard.png) | ![Báo cáo](mockup/ui-reports.png) | ![Cài đặt](mockup/ui-settings.png) |

## Kiến trúc

```
├── frontend/              # React 18 + Vite 5 (login, upload, danh sách, báo cáo)
│   └── src/               # Login.jsx, Invoices.jsx, api.js (Bearer token)
├── src/
│   ├── auth/security.py   # pbkdf2 hash + JWT (PyJWT)
│   ├── llm/base.py        # LLM provider (OpenAI/Mock) cho fallback
│   ├── domain/models.py   # Invoice, User, Token, MonthlyReport
│   ├── extract/extractor.py  # Regex song ngữ + OCR + LLM fallback
│   ├── store/repository.py   # SQLite CRUD + báo cáo tháng (scoped theo user)
│   ├── store/users.py     # UserRepository
│   └── app.py             # /auth/* + /invoices/* (JWT protected)
└── tests/                 # 42 tests (unit + API integration)
```

## Bắt đầu nhanh

```bash
pip install -r requirements.txt
python main.py        # chạy demo end-to-end
python -m uvicorn src.app:app --port 8004
```

LLM fallback (tùy chọn): tạo file `.env` (đã có sẵn key mẫu — thay bằng key của bạn):

```env
LLM_BASE_URL=https://api.qwencoder.cloud/api/v1
LLM_API_KEY=qwk_...
LLM_MODEL=qwen3.7-max
```

Regex đọc đủ (confidence ≥ 0.8) → không gọi LLM. Chỉ thiếu trường → LLM lấp chỗ.

Swagger UI: http://localhost:8004/docs

## API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/invoices/upload` | Upload file hóa đơn → trích xuất → lưu |
| POST | `/invoices` | Tạo hóa đơn thủ công |
| GET | `/invoices` | Liệt kê hóa đơn (lọc theo status) |
| GET | `/invoices/{id}` | Lấy hóa đơn theo id |
| PATCH | `/invoices/{id}` | Cập nhật hóa đơn (vd: đánh dấu paid) |
| DELETE | `/invoices/{id}` | Xóa hóa đơn |
| GET | `/reports/monthly/{YYYY-MM}` | Báo cáo theo tháng |

## Tests

```bash
pytest tests/ -v
```

28 tests: trích xuất (Anh + GTGT Việt), OCR, nhiều mức thuế, chiết khấu, CRUD, auth JWT, cách ly đa user.

## Chạy

```bash
# Backend + UI (đã build sẵn trong dist)
pip install -r requirements.txt
python -m uvicorn src.app:app --port 8004

# Frontend dev (hot reload) — tùy chọn
cd frontend && npm install && npm run dev   # → http://localhost:5173
```

UI: http://localhost:8004 — đăng ký tài khoản → upload hóa đơn → báo cáo.

## OCR (tùy chọn)

Cài để đọc ảnh scan hóa đơn:
```bash
pip install pytesseract paddleocr Pillow
# + cài đặt engine: tesseract-ocr (lang vie) hoặc paddleocr
```
Không cài vẫn chạy — chỉ mất tính năng đọc ảnh.

## Khách hàng mục tiêu

- Kế toán, freelancer, SME cần quản lý hóa đơn đầu vào
- Trả phí hàng tháng (SaaS) cho việc tiết kiệm giờ nhập liệu thủ công
