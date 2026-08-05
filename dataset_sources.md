# Dataset sources — dữ liệu thật dùng cho benchmark

## 1. SROIE (ICDAR 2019) — 987 receipt scan thật (committed, 2.2MB)

- Dataset công khai: ICDAR 2019 Scanned Receipts OCR and Information Extraction
- Nguồn: mirror `jsdnrs/ICDAR2019-SROIE` (Kaggle/GitHub) — OCR words + bboxes + GT company/date/total
- Local: `data/sroie/train.jsonl` (626) + `test.jsonl` (361) = 987 hóa đơn/receipt tiếng Anh thật (Malaysia)
- Benchmark: `tests/benchmark_sroie.py` — tái dựng dòng text từ bboxes → extract regex → so sánh GT

## 2. MCOCR 2021 — 60 hóa đơn Việt Nam thật (15MB, gitignored — tải lại tự động)

- Dataset: MCOCR 2021 (Vietnamese OCR competition, công khai) — hóa đơn Co.opmart/VinCommerce/minimart thật
- Mirror GitHub: `TanDuong986/GCN_Vietnamese_invoice` → `Vietnam_invoice_data/mcocr2021_raw/mcocr_train_data/` (ảnh + `mcocr_train_df.csv` có label SELLER/ADDRESS/TIMESTAMP/TOTAL_COST + image quality)
- Sample: 60 ảnh quality ≥ 0.6 (deterministic, mỗi ảnh thứ ~15) → `data/mcocr_sample/` (gitignored)
- Benchmark: `tests/benchmark_mcocr.py` — **tự tải lại ảnh nếu thiếu**; pipeline ảnh → PaddleOCR(vi) → regex

## 3. Legacy synthetic GTGT (committed) — KHÔNG phải dữ liệu thật

- `tests/benchmark_gtgt.py` + `tests/benchmark_ocr.py`: hóa đơn tự sinh (text/PIL-render)
- Giữ để test nhanh regex VN — superseded bởi SROIE + MCOCR cho mục đích benchmark

## Verify

```bash
sha256sum -c data/checksums.sha256
python tests/benchmark_sroie.py    # 987 receipt thật
python tests/benchmark_mcocr.py    # 60 hóa đơn VN thật (tự tải data nếu thiếu)
pytest tests/test_sroie_regression.py
```
