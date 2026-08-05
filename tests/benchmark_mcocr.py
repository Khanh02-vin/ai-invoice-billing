"""Benchmark trích xuất trên HÓA ĐƠN VIỆT NAM THẬT (MCOCR 2021).

Data: MCOCR 2021 (public OCR dataset từ AIC 2021, mirror GitHub
      TanDuong986/GCN_Vietnamese_invoice) — 1.154 hóa đơn Việt Nam thật
      (Co.opmart, VinCommerce, minimart...) có label SELLER/ADDRESS/
      TIMESTAMP/TOTAL_COST + image quality.
Sample: 60 ảnh quality >= 0.6 (deterministic, mỗi ảnh thứ ~15).
Pipeline: ảnh → PaddleOCR (vi) → regex extract (không LLM) → so sánh
          vendor / date / total với GT từ label của dataset.
Chạy: python tests/benchmark_mcocr.py > tests/mcocr_result.txt
"""
import ast
import csv
import io
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract.extractor import extract_from_text, read_file_text, _normalize_date, _chain_name
from tests.benchmark_sroie import norm_company, norm_total

DATA = Path(__file__).parent.parent / "data" / "mcocr_sample"
CSV_URL = "https://raw.githubusercontent.com/TanDuong986/GCN_Vietnamese_invoice/main/Vietnam_invoice_data/mcocr2021_raw/mcocr_train_data/mcocr_train_df.csv"
IMG_URL = "https://raw.githubusercontent.com/TanDuong986/GCN_Vietnamese_invoice/main/Vietnam_invoice_data/mcocr2021_raw/mcocr_train_data/train_images/{}"
SAMPLE_N = 60
QUALITY_MIN = 0.6

_DATE_RE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b")
_NUM_RE = re.compile(r"[\d.,]+")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read()


def prepare():
    """Download CSV + sample ảnh nếu chưa có."""
    DATA.mkdir(exist_ok=True)
    csvp = DATA / "mcocr_train_df.csv"
    if not csvp.exists():
        csvp.write_bytes(fetch(CSV_URL))
    rows = list(csv.DictReader(io.StringIO(csvp.read_text(encoding="utf-8"))))
    rows = [r for r in rows if float(r["anno_image_quality"]) >= QUALITY_MIN]
    # sample deterministic: stride để lấy ~SAMPLE_N
    stride = len(rows) // SAMPLE_N
    sample = rows[::stride][:SAMPLE_N]
    got = 0
    for r in sample:
        img = DATA / r["img_id"]
        if not img.exists():
            img.write_bytes(fetch(IMG_URL.format(r["img_id"])))
            got += 1
    return sample, got


def gt_from_row(r):
    """GT từ label: SELLER → vendor, TIMESTAMP → date, TOTAL_COST → total."""
    texts = r["anno_texts"].split("|||")
    labels = r["anno_labels"].split("|||")
    vendor = date = total = None
    for label, text in zip(labels, texts):
        if label == "SELLER" and vendor is None:
            vendor = text
        elif label == "TIMESTAMP" and date is None:
            m = _DATE_RE.search(text)
            if m:
                date = _normalize_date(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
        elif label == "TOTAL_COST" and total is None:
            m = _NUM_RE.search(text.replace(".", ""))
            if m:
                total = norm_total(m.group(0))
    return vendor, date, total


def main():
    sample, downloaded = prepare()
    print("=" * 72)
    print("INVOICE EXTRACTION — HÓA ĐƠN VIỆT NAM THẬT (MCOCR 2021)")
    print(f"Sample: {len(sample)} ảnh (quality >= {QUALITY_MIN}), tải mới {downloaded}")
    print("Pipeline: ảnh → PaddleOCR(vi) → regex (không LLM)")
    print("=" * 72)

    # Pass 1: OCR text cache — PaddleOCR CPU nondeterminism ±4% giữa các lần chạy.
    # Cache text sau lần OCR đầu → rerun (kể cả +LLM) deterministic, so sánh công bằng.
    txt_dir = DATA / "text_cache"
    txt_dir.mkdir(exist_ok=True)
    texts = {}
    for r in sample:
        p = txt_dir / (r["img_id"] + ".txt")
        if p.exists():
            texts[r["img_id"]] = p.read_text(encoding="utf-8")
        else:
            text = read_file_text(str(DATA / r["img_id"]))
            p.write_text(text, encoding="utf-8")
            texts[r["img_id"]] = text

    llm = None
    if os.getenv("BENCH_LLM"):
        from tests.llm_cache import CachingProvider, CACHE_DIR
        llm = CachingProvider(CACHE_DIR / "llm_cache_mcocr.jsonl")
        n = llm.preload([texts[r["img_id"]] for r in sample])
        print(f"LLM mode={os.getenv('LLM_MODE', 'fill')}: {n} API call mới, "
              f"cache {len(llm.cache)} entry")

    stats = {"vendor": [0, 0], "date": [0, 0], "total": [0, 0]}
    fails = []
    t0 = time.perf_counter()
    for r in sample:
        gt_v, gt_d, gt_t = gt_from_row(r)
        try:
            inv = extract_from_text(texts[r["img_id"]], llm=llm)
        except Exception as e:
            print(f"  CRASH {r['img_id']}: {e}")
            continue
        if gt_v:
            stats["vendor"][1] += 1
            # GT cũng qua từ điển chuỗi: GT OCR lệch tên hãng (VD "THE COFFEE HQUSE")
            # được chuẩn hóa về thương hiệu — so khớp công bằng 2 phía ở mức chuỗi.
            if norm_company(inv.vendor) == norm_company(_chain_name(gt_v)):
                stats["vendor"][0] += 1
            else:
                fails.append(("vendor", r["img_id"], gt_v, inv.vendor))
        if gt_d:
            stats["date"][1] += 1
            if inv.issue_date == gt_d:
                stats["date"][0] += 1
            else:
                fails.append(("date", r["img_id"], gt_d, inv.issue_date))
        if gt_t is not None:
            stats["total"][1] += 1
            if abs(inv.total - gt_t) < 0.01:
                stats["total"][0] += 1
            else:
                fails.append(("total", r["img_id"], gt_t, inv.total))

    dt = time.perf_counter() - t0
    total_ok = sum(v[0] for v in stats.values())
    total_den = sum(v[1] for v in stats.values())
    print(f"\n{'field':<8}{'matched':>8}{'total':>8}{'accuracy':>10}")
    for k, (ok, den) in stats.items():
        print(f"{k:<8}{ok:>8}{den:>8}{ok / den * 100:>9.1f}%")
    print(f"{'overall':<8}{total_ok:>8}{total_den:>8}{total_ok / total_den * 100:>9.1f}%")
    print(f"\nTime: {dt:.0f}s ({dt / len(sample):.1f}s/ảnh)")
    print(f"\n--- {min(len(fails), 20)} fail đầu tiên (field | img | expected | got) ---")
    for field, img, exp, got in fails[:20]:
        print(f"{field:<7} {img[:24]}  exp={exp!r}  got={got!r}")


if __name__ == "__main__":
    main()
