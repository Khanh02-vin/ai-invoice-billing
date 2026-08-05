"""Benchmark trích xuất trên RECEIPT THẬT (CORD v2, CC-BY-4.0).

Data: naver-clova-ix/cord-v2 (HuggingFace, 800 receipt train) — ảnh scan
      thật + GT JSON {gt_parse: {menu, sub_total, total}}.
Pipeline: ảnh → PaddleOCR (vi) → regex extract (không LLM) → so sánh
          total với GT.
Giới hạn GT CORD (ghi thẳng): annotation KHÔNG có vendor/company và KHÔNG
      có date — chỉ có menu + sub_total + total → benchmark đo duy nhất
      field `total` (extractor không có field sub_total riêng).

Chạy: python tests/benchmark_cord.py > tests/cord_result.txt
Require: pip install datasets (benchmark-only, không bắt buộc cho app)
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract.extractor import extract_from_text, read_file_text

DATA = Path(__file__).parent.parent / "data" / "cord_sample"
SAMPLE_N = 60


def img_id_of(row) -> str:
    """image_id từ meta trong ground_truth JSON."""
    try:
        return str(json.loads(row["ground_truth"])["meta"]["image_id"])
    except (KeyError, TypeError, ValueError):
        return ""


def gt_total(row) -> float:
    """GT total từ json ground_truth → float. None nếu thiếu.

    GT CORD v2 KHÔNG nhất quán quy ước số (phát hiện khi benchmark, ghi thẳng):
      - comma-thousands: "1,591,600" (40/57 mẫu)
      - dot-thousands kiểu Hàn: "61.500" = 61.500 won (13/57)
      - tiền tệ "Rp": "Rp 16.500", "Rp. 20.000"
      - hỗn hợp EU: "62.000,00" (chấm nghìn + phẩy thập phân)
    norm_total (SROIE/MCOCR) coi chấm là thập phân → sai 1000x cho dot-thousands.
    Parser riêng: tổng tiền là số nguyên won → bỏ mọi separator theo quy ước.
    """
    try:
        d = json.loads(row["ground_truth"])
        s = str(d["gt_parse"]["total"]["total_price"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    s = s.replace("Rp", "").replace("rp", "").strip()
    if "," in s and "." in s:
        # hỗn hợp EU: chấm nghìn, phẩy thập phân → 62.000,00 = 62000.00
        s = s.replace(".", "").replace(",", ".")
        return float(s)
    if "," in s:
        s = s.replace(",", "")
        return float(s)
    if "." in s and len(s.split(".")[-1]) == 3:
        s = s.replace(".", "")  # dot-thousands kiểu Hàn
    try:
        return float(s)
    except ValueError:
        return None


def main():
    try:
        from datasets import load_dataset
    except ImportError:
        print("Thiếu datasets lib: pip install datasets (chỉ cho benchmark CORD)")
        sys.exit(1)

    ds = load_dataset("naver-clova-ix/cord-v2", split="train")
    # sample deterministic: stride để lấy ~SAMPLE_N từ 800 ảnh
    stride = len(ds) // SAMPLE_N
    sample_idx = list(range(0, len(ds), stride))[:SAMPLE_N]

    DATA.mkdir(exist_ok=True)
    txt_dir = DATA / "text_cache"
    txt_dir.mkdir(exist_ok=True)

    print("=" * 72)
    print("INVOICE EXTRACTION — RECEIPT THẬT (CORD v2, CC-BY-4.0)")
    print(f"Sample: {len(sample_idx)} receipt / {len(ds)} train")
    print("Pipeline: ảnh → PaddleOCR → regex (không LLM)")
    print("GT CORD chỉ có total (không vendor/date) → đo mỗi field total")
    print("=" * 72)

    # Pass 1: save ảnh + OCR text cache — PaddleOCR CPU nondeterminism ±4%
    texts = {}
    t0 = time.perf_counter()
    for i in sample_idx:
        row = ds[i]
        img_id = img_id_of(row)
        p = txt_dir / f"{img_id}.txt"
        if p.exists():
            texts[img_id] = p.read_text(encoding="utf-8")
        else:
            row["image"].save(DATA / f"{img_id}.png")
            text = read_file_text(str(DATA / f"{img_id}.png"))
            p.write_text(text, encoding="utf-8")
            texts[img_id] = text

    llm = None
    if os.getenv("BENCH_LLM"):
        from tests.llm_cache import CachingProvider, CACHE_DIR
        llm = CachingProvider(CACHE_DIR / "llm_cache_cord.jsonl")
        ids = [img_id_of(ds[i]) for i in sample_idx]
        n = llm.preload([texts[i] for i in ids])
        print(f"LLM mode={os.getenv('LLM_MODE', 'fill')}: {n} API call mới, "
              f"cache {len(llm.cache)} entry")

    ok = den = 0
    fails = []
    t1 = time.perf_counter()
    for i in sample_idx:
        row = ds[i]
        img_id = img_id_of(row)
        gt_t = gt_total(row)
        if gt_t is None:
            print(f"  SKIP {img_id}: GT thiếu total")
            continue
        den += 1
        try:
            inv = extract_from_text(texts[img_id], llm=llm)
        except Exception as e:
            print(f"  CRASH {img_id}: {e}")
            continue
        if abs(inv.total - gt_t) < 0.01:
            ok += 1
        else:
            fails.append((img_id, gt_t, inv.total))

    dt = time.perf_counter() - t1
    print(f"\n{'field':<8}{'matched':>8}{'total':>8}{'accuracy':>10}")
    print(f"{'total':<8}{ok:>8}{den:>8}{ok / den * 100:>9.1f}%")
    print(f"\nTime OCR+extract: {dt:.0f}s ({dt / den:.1f}s/ảnh)")
    print(f"\n--- {min(len(fails), 20)} fail đầu tiên (img | expected | got) ---")
    for img, exp, got in fails[:20]:
        print(f"{img}  exp={exp!r}  got={got!r}")


if __name__ == "__main__":
    main()
