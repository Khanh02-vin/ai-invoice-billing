# GAP_ANALYSIS — trạng thái hiện tại vs. kiến trúc đích (local-first)

Ngày: 2026-11-07 · commit nền: `bfcc644` (branch `exp1`) · test nền: **300 passed**

Tài liệu này là **căn cứ quyết định**, không phải danh sách việc. Mỗi gap ghi: bằng chứng (file:line), tác động, và lựa chọn xử lý kèm chi phí tương đối. Đọc cùng [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md) và [ADR-0001](adr/ADR-0001-local-first-modular-monolith.md), [ADR-0002](adr/ADR-0002-location-model.md), [ADR-0003](adr/ADR-0003-database-postgres-postgis.md).

Nguyên tắc chọn (theo ponytail): YAGNI · sửa gốc chung · diff nhỏ nhất · xóa trước thêm.

---

## Bảng xếp hạng

| Hạng | Gap | Loại | Chi phí | Đề xuất |
|------|-----|------|---------|---------|
| G1 | LLM fallback không được nối vào `extract_invoice` | Bug wiring | Rất nhỏ | **Vá ngay** |
| G2 | Mô hình vị trí ADR-0002 là schema chết (9 field, 0 writer) | Quyết định kiến trúc | Nhỏ–vừa | **Chốt: nối hoặc rút** |
| G3 | Client không gửi accuracy/source/captured_at | Thiếu dữ liệu | Nhỏ | Gộp vào G2 |
| G4 | Link/unlink giao dịch↔hóa đơn có API nhưng UI không gọi | Thiếu UI | Nhỏ | Vá khi có nhu cầu thật |
| G5 | `evidence_refs` là cột chết | Schema chết | Rất nhỏ | Xóa hoặc nối cùng G2 |
| G6 | `/upload` fail cứng khi hóa đơn thiếu số HĐ | Robustness | Nhỏ | Vá |
| G7 | Offline/sync: bảng outbox + ADR nhưng 0 luồng | Hạng mục lớn | Lớn | **Quyết định có làm không** |
| G8 | Service worker không cache → không offline | PWA | Vừa | Phụ thuộc G7 |
| G9 | Chưa có PostGIS/H3/chỉ mục không gian | Hạ tầng | Lớn | Chờ G7 hoặc khi >10k điểm |
| G10 | Không có phân tích chi tiêu (category/merchant/tháng) | Thiếu tính năng | Vừa | Làm khi có nhu cầu |
| G11 | Điểm gần đây gọi OSM trực tiếp, không cache, không bảng POI | Hiệu năng/ổn định | Vừa | Gộp G2 (MerchantLocation) |
| G12 | Không có động cơ giá/đánh giá cho gợi ý | Thiếu tính năng | Lớn | YAGNI cho tới khi có dữ liệu cộng đồng |
| G13 | Rác debug: `print()` trong `_do_extract`; hai `def extract_from_text` trùng tên | Vệ sinh | Rất nhỏ | Dọn |

---

## G1 — LLM fallback chết ở production · **vá ngay**

**Bằng chứng.** `extract_invoice()` gọi `extract_from_text(text, source_file=source_file, layout=layout)` — **không truyền `llm=`** (`src/extract/extractor.py:728`). Nhánh LLM chỉ chạy khi `llm is not None` (`extractor.py:1466`). Vì vậy toàn bộ các đường vào thật đều bỏ qua LLM:

- `POST /upload` → `_do_extract` → `extract_fn` (`src/workers/extraction_worker.py:110-116`)
- `POST /payments/camera-upload` (`src/routers/payments.py:897`)
- IMAP poller (`src/payments/imap_poller.py:312`)
- batch (`src/extract/batch_extractor.py:21`)

Chỉ `tests/test_llm_fallback.py`, `tests/benchmark_*.py` truyền `llm=`.

**Tác động.** Docstring ở `payments.py:877` ghi "Uses existing extract_invoice pipeline with LLM fallback" — **sai**. Hóa đơn có layout lạ (không khớp regex) sẽ không được LLM cứu; người dùng thấy "không đọc được" mà không biết vì sao.

**Đề xuất.** Trong `extract_invoice`, nạp provider và truyền vào — có guard để không nổ khi chưa cấu hình LLM:

```python
from ..llm.base import get_llm_provider
invoice = extract_from_text(text, source_file=source_file, layout=layout,
                            llm=get_llm_provider())
```

`_extract_llm` đã tự bọc `try/except` trả `{}` (`extractor.py:1532-1541`) và chỉ gọi khi `confidence < _llm_threshold()`, nên chi phí thêm ~0 khi regex đã đủ tốt. Thêm 1 test: hóa đơn rác + provider giả → field được điền.

---

## G2 — Mô hình vị trí ADR-0002 chưa được thi công · **quyết định**

**Bằng chứng.** 9 field khai báo trên 3 entity:

- `Invoice`: `models.py:147-164`
- `PaymentTransaction`: `models.py:241-258`
- `Purchase`: `models.py:323-340`

Gồm `payment_accuracy_m`, `payment_source`, `payment_provider`, `merchant_poi_id`, `merchant_name`, `merchant_address`, `merchant_lat`, `merchant_lon`, `merchant_source`, `merchant_verified_by_user`, `purchase_context_type`, `user_confirmed_location`.

Đã migrate ở `sql_repository.py:1482-1498`. **Grep toàn repo: 0 writer, 0 reader** (ngoài khai báo + ALTER TABLE).

**Tác động.** Schema nói một đằng, hành vi một nẻo. Người đọc sau tưởng vị trí đã có độ chính xác/nguồn gốc, thực tế chỉ có lat/lon. ADR-0002 mô tả một hệ thống không tồn tại.

**Hai lựa chọn (chọn 1, đừng lấp lửng):**

- **(a) Nối** (khuyến nghị nếu vị trí là tính năng cốt lõi): mở rộng `TransactionCreate/Update` (`payments.py:245-247,266-268`) và `POST /purchases/manual` (`payments.py:1047-1050`) nhận `accuracy/source/captured_at`; client gửi `coords.accuracy` + `source:"gps"`; set `merchant_*` từ reverse-geocode hiện có. Diff: ~4 file, không migration mới.
- **(b) Rút**: xóa 9 field khỏi model + bỏ khối ALTER; sửa ADR-0002 thành "chưa làm". Diff nhỏ, giảm tải nhận thức.

Không chọn "để đó" — đó là trạng thái đắt nhất.

---

## G3 — Client chỉ gửi lat/lon

**Bằng chứng.** `Ledger.jsx:60` gửi `{location_lat, location_lon, location_captured_at}`; `Purchases.jsx:38` chỉ `{location_lat, location_lon}`; `HistoryTimeline.jsx:66` không gửi gì (chỉ để căn map). Không nơi nào đọc `coords.accuracy`.

**Tác động.** Không phân biệt được GPS 5m với định vị mạng 2km; timeline vẽ "hành trình" từ điểm nhiễu.

**Đề xuất.** Là phần của G2(a). `getCurrentPosition` đã trả `coords.accuracy` sẵn — thêm 1 field, không thêm code định vị.

---

## G4 — Link/unlink giao dịch↔hóa đơn thiếu UI

**Bằng chứng.** API đầy đủ: `POST /transactions/{id}/invoices` (`payments.py:422-443`), `DELETE .../invoices/{invoice_id}` (`payments.py:446-466`) — có kiểm tra chặn vượt số tiền (`441-442`). Audit frontend: **NOT CALLED** ở bất kỳ component nào.

**Tác động.** Khi auto-match ra `ambiguous` (`matching.py:168-175`), giao dịch giữ `pending_receipt` — nhưng người dùng **không có cách nào ghép thủ công trên UI**. Chỉ có nút dismiss (`Invoices.jsx:227`). Đây là lỗ hổng chặn luồng: tiền vào, hóa đơn có, mà không nối được.

**Đề xuất.** Thêm nút "Ghép hóa đơn" ở màn giao dịch pending, gọi endpoint đã có. Không cần API mới.

---

## G5 — `evidence_refs` là cột chết

**Bằng chứng.** `models.py:163,257,339` (JSON list); migrate `sql_repository.py:1482-1498`. 0 writer.

**Đề xuất.** Xóa cùng G2(b), hoặc nối cùng G2(a) nếu ADR-0002 cần chuỗi bằng chứng. Một mình nó không đủ để tồn tại.

---

## G6 — `/upload` fail cứng khi hóa đơn thiếu số HĐ

**Bằng chứng.** `extraction_worker.py:111-112`:
```python
if not invoice or not invoice.invoice_number or invoice.total <= 0:
    raise ValueError("Could not extract valid invoice data")
```
→ job `FAILED` + Celery retry 3 lần. Trong khi `/camera-upload` dùng tiêu chí khoan dung hơn — chỉ fail khi **cả 5** dấu hiệu trống (`payments.py:907-920`).

**Tác động.** Nhiều hóa đơn bán lẻ VN **không có số hóa đơn**. Luồng `/upload` sẽ từ chối chúng dù đọc được vendor + total; `/camera-upload` thì nhận. Hai luồng cùng trích xuất một hóa đơn cho kết quả trái ngược.

**Đề xuất.** Dùng chung một hàm kiểm tra "unreadable" cho cả hai đường (sửa gốc chung), điều kiện = thiếu cả vendor + total + ngày + line_items, không chỉ số HĐ.

---

## G7 — Offline/sync: có ADR và bảng, không có luồng · **quyết định lớn nhất**

**Bằng chứng.**

- `sync_outbox` (id, user_id, entity_type, entity_id, operation, payload, status, retry_count, last_error, created_at, synced_at) + 2 index: `sql_repository.py:1511-1532`
- `sync_cursor` (user_id PK, last_sync_at, server_cursor): `sql_repository.py:1535-1541`
- **0 hit** cho `sync_outbox`/`sync_cursor`/`/sync` ngoài DDL. Không route, không repository method.
- Client: không `indexedDB`, không queue, không `navigator.onLine`.

**Tác động.** ADR-0001 đặt local-first làm trục, nhưng không có tầng đồng bộ nào. Bảng rỗng tạo cảm giác "đã làm" — rủi ro nhận thức cao nhất trong repo.

**Đề xuất.** Quyết định dứt khoát:

- Nếu local-first nằm trong 2 quý tới → đây là hạng mục lớn nhất; thiết kế sync protocol (push outbox / pull cursor) trước khi viết thêm tính năng vị trí.
- Nếu chưa → **xóa 2 bảng + bỏ ADR-0001 hoặc đánh dấu Deferred**. Giữ chúng "chờ" tốn ít code nhưng tốn nhiều tin cậy.

---

## G8 — Service worker không cache

**Bằng chứng.** `frontend/public/sw.js:6-8` ghi thẳng: `// Không cache gì — fetch handler rỗng`. Chỉ xử lý `push`/`notificationclick` (`sw.js:10-25`). `frontend/src/push.js` register SW chỉ để lấy push subscription.

**Tác động.** App cài như PWA nhưng mất mạng là trắng trang — trải nghiệm "cài app" bị phá.

**Đề xuất.** Phụ thuộc G7. Nếu làm offline: cache app shell (stale-while-revalidate) là bước rẻ nhất và độc lập với sync dữ liệu.

---

## G9 — Chưa có PostGIS / H3 / chỉ mục không gian

**Bằng chứng.** `config.py:209` `database_path = "invoices.db"` (SQLite); `store/db.py` chỉ SQLite WAL. `postgis/ST_/geometry/h3/geohash/rtree` → 0 trong code, chỉ trong docs ADR-0003. Khoảng cách dùng haversine Python (`payments.py:681-693`).

**Tác động.** Hiện tại **chưa phải vấn đề** — haversine trên vài nghìn điểm là đủ. Trở thành vấn đề khi lọc "trong bán kính" trên dữ liệu lớn hoặc cần `GROUP BY` không gian.

**Đề xuất.** Ponytail: **không làm bây giờ**. ADR-0003 có thể giữ làm "Deferred". Đặt ngưỡng kích hoạt rõ ràng (ví dụ: khi bảng vị trí > 10k dòng hoặc p95 `/timeline` > 500ms) thay vì làm trước.

---

## G10 — Không có phân tích chi tiêu

**Bằng chứng.** Chỉ `GET /reports/monthly/{period}` (`app.py:624`) trả danh sách hóa đơn + tổng, và PDF (`reports/pdf_exporter.py`). Không endpoint aggregate theo category/merchant/tháng cho giao dịch/mua hàng.

**Tác động.** Dữ liệu để phân tích đã có (`Purchase.category`, `merchant`, `occurred_at`) — chỉ thiếu truy vấn.

**Đề xuất.** Khi có nhu cầu: một endpoint + một `GROUP BY`. Không cần bảng mới, không cần pipeline.

---

## G11 — Điểm gần đây gọi OSM trực tiếp

**Bằng chứng.** `payments.py:564-581` gọi `https://nominatim.openstreetmap.org/search` mỗi request (timeout 4s), lọc khoảng cách phía sau, không cache, không bảng POI local.

**Tác động.** Phụ thuộc mạng ngoài + vi phạm chính sách rate-limit của Nominatim khi nhiều người dùng.

**Đề xuất.** Gộp vào G2(a): `MerchantLocation` (đã khai trong model!) chính là bảng POI local cần có — dùng nó làm cache/replace cho OSM.

---

## G12 — Không có động cơ giá/đánh giá

**Bằng chứng.** `places/suggestions` chỉ đếm lượt ghé (`payments.py:766,775`) + headcount cộng đồng (`794`). `purchase_memory` là nhận diện, không phải gợi ý (`purchase_memory.py:152-175`).

**Đề xuất.** Ponytail: **YAGNI**. Chưa đủ dữ liệu cộng đồng để giá/đánh giá có nghĩa (ngưỡng hiện tại ≥2 người/địa điểm). Để heuristic chạy và thu dữ liệu trước.

---

## G13 — Vệ sinh code

**Bằng chứng.**

- `print(f"DEBUG _do_extract: ...")` và `print(f"DEBUG: invoice.provenance = ...")` trong `extraction_worker.py:88,113` — rác debug còn sót.
- **Hai** `def extract_from_text` cùng module: `extractor.py:565` (regex-only, tham số `layout` không có) và `extractor.py:1455` (có `llm`, `layout`). Bản ở 565 bị **shadow**, không ai gọi → dead code ~150 dòng.
- `_make_id` (`extractor.py:1353`) chỉ còn dùng ở tests, vì `extract_invoice` ghi đè `invoice.id = uuid4()` (`extractor.py:737`).

**Đề xuất.** Xóa `print`, xóa `extract_from_text` bản 565, đánh dấu/xóa `_make_id` nếu test cho phép. Diff nhỏ, giảm ~160 dòng và một bẫy shadowing.

---

## Phụ lục — trạng thái hạ tầng đã xác minh

| Hạng mục | Trạng thái | Bằng chứng |
|----------|-----------|------------|
| Async extraction (Celery) | Có | `workers/celery_app.py`, `extraction_worker.py:230-269`; enqueue ở `app.py:584-600` |
| Fallback đồng bộ khi không có broker | Có | `app.py:602-616` (`_do_extract` chạy inline) |
| IMAP poller | Có, tùy chọn | `app.py:133-147`; chỉ chạy khi cấu hình `IMAP_HOST` |
| Push (Web Push/VAPID) | Có | `src/push.py`, `routers/push.py`, `frontend/src/push.js` |
| Storage file local + validate | Có | `src/upload.py`; `LocalFileStorage` |
| Multi-tenant org + scope | Có | `routers/organizations.py`; scope trong mọi truy vấn (`user_id=`, `organization_id=`) |
| Rate limit sau proxy | Có | commit `bf1c6d1`; `rate_limit_dependency` dùng ở các route upload |
| Test backend | 300 passed | `python3 -m pytest -q` (2026-11-07) |
| Test frontend | **Không có** | `frontend/package.json` không có script `test`, không vitest/jest/playwright |

---

## Thứ tự đề xuất

1. **G1** (vá LLM — một tham số, rủi ro thấp, sửa lời hứa sai trong docstring).
2. **G2 + G3 + G5 + G11** (một quyết định về vị trí, gộp 4 gap: nối hoặc rút).
3. **G13 + G6** (vệ sinh + robustness, diff nhỏ, sửa gốc chung của hai luồng upload).
4. **G7** (quyết định local-first: làm hay xóa — ảnh hưởng G8, G9).
5. Còn lại (G4, G10, G12) theo nhu cầu thật, không làm trước.
