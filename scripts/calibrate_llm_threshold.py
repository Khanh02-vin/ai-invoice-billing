"""Calibrate ngưỡng gọi LLM (confidence < threshold) bằng ground truth SROIE.

Vấn đề: 0.8 là số đặt cảm tính. Quá thấp → gọi LLM cả receipt đọc tốt (tốn token);
quá cao → bỏ sót receipt đọc sai. Script đo trên 987 receipt THẬT có GT
(company/date/total) và in bảng đánh đổi theo từng ngưỡng:

    %gọi LLM  = chi phí (token): % receipt phải qua LLM
    recall    = trong các receipt CÓ lỗi, % được gọi LLM (có cơ hội sửa)
    precision = trong các receipt bị gọi, % thực sự có lỗi (đỡ tốn token vô ích)
    bỏ sót    = receipt có lỗi nhưng không được gọi → lỗi không thể sửa

Ngưỡng gợi ý = KNEE của đường (chi phí → recall): điểm xa đường thẳng nối
(0,0) với điểm cuối nhất về phía "lợi nhiều so với chi". Sau knee, mỗi bước
tăng ngưỡng đòi thêm rất nhiều call mà recall tăng ít.

Kết quả in ra được áp bằng env LLM_CALL_THRESHOLD (default trong
src/extract/extractor.py::_llm_threshold đọc env này).

Chạy: python scripts/calibrate_llm_threshold.py
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract.extractor import extract_from_text, _normalize_date

DATA = Path(__file__).parent.parent / "data" / "sroie"
_THRESHOLDS = [0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
CORE_FIELDS = ("invoice_number", "vendor", "issue_date", "total", "tax")


def _reconstruct_lines(words, bboxes, height):
    """Giống tests/benchmark_sroie.py: gom token cùng y → sort theo x."""
    tol = max(6, height * 0.015)
    tokens = sorted(zip(words, bboxes), key=lambda t: (t[1][1], t[1][0]))
    lines = []
    for word, box in tokens:
        y1 = box[1]
        if lines and abs(y1 - lines[-1][0]) <= tol:
            lines[-1][1].append((box[0], word))
        else:
            lines.append([y1, [(box[0], word)]])
    return [" ".join(w for _, w in sorted(tok)) for _, tok in lines]


def _norm_company(s: str) -> str:
    """Giống benchmark: bỏ space/dấu/mã đăng ký → so chuỗi chữ."""
    if not s:
        return ""
    s = re.sub(r"\s*\([0-9]{4,6}-[A-Za-z]\)", "", s)
    s = re.sub(r"\s*\(\s*[\dA-Z-]*-?[\d]+\s*\)\s*$", "", s.strip())
    s = re.sub(r"\s+", "", "".join(
        c for c in unicodedata.normalize("NFD", s.strip(" .,;:/\\\"'()-"))
        if not unicodedata.combining(c)))
    return s.upper()


def _norm_total(s):
    if s is None:
        return None
    try:
        return float(str(s).replace("$", "").replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def field_errors(inv, rec) -> dict:
    """Field nào sai so GT — dùng để đếm lỗi + thống kê theo route."""
    ent = rec.get("entities") or {}
    bad = {}
    gt_total = _norm_total(ent.get("total"))
    if gt_total is not None:
        bad["total"] = abs(inv.total - gt_total) >= 0.01
    gt_company = _norm_company(ent.get("company"))
    if gt_company:
        bad["vendor"] = _norm_company(inv.vendor) != gt_company
    gt_date = ent.get("date")
    if gt_date:
        bad["date"] = inv.issue_date != _normalize_date(gt_date)
    return bad


def knee(points):
    """Điểm "lợi nhất so với đường thẳng nối đầu–cuối": khoảng cách ĐỨNG cao nhất
    phía trên chord. Cả 2 trục đều là % (0-100) nên so trực tiếp được."""
    (x0, y0), (x1, y1) = points[0], points[-1]
    dx = x1 - x0
    slope = (y1 - y0) / dx if dx else 0.0
    best, best_d = points[0], float("-inf")
    for p in points:
        d = (p[1] - y0) - (p[0] - x0) * slope   # dương = trên chord = lợi hơn tuyến tính
        if d > best_d:
            best, best_d = p, d
    return best


def main():
    recs = []
    for split in ("train", "test"):
        for line in (DATA / f"{split}.jsonl").open(encoding="utf-8"):
            recs.append(json.loads(line))
    print(f"SROIE: {len(recs)} receipt thật (GT company/date/total)\n")

    rows = []          # (confidence, có_lỗi, provenance)
    err_by_field = {}  # (field, source, conf) -> [n, n_err]
    for rec in recs:
        text = "\n".join(_reconstruct_lines(
            rec["words"], rec["bboxes"], rec["image_size"]["height"]))
        inv = extract_from_text(text)
        bad = field_errors(inv, rec)
        rows.append((inv.confidence, any(bad.values())))
        for field, is_bad in bad.items():
            p = inv.provenance.get(field)
            key = (field, p.source if p else "-", round(p.confidence, 2) if p else 0.0)
            slot = err_by_field.setdefault(key, [0, 0])
            slot[0] += 1
            slot[1] += int(is_bad)

    n = len(rows)
    n_err = sum(1 for _, bad in rows if bad)
    print(f"Regex sai ≥1 field: {n_err}/{n} ({n_err / n * 100:.1f}%)\n")

    print(f"{'ngưỡng':>7} {'%gọi LLM':>9} {'recall':>8} {'precision':>10} {'bỏ sót':>8}")
    seen, points, labels = set(), [], []
    for t in _THRESHOLDS:
        called = [(c, b) for c, b in rows if c < t]
        rate = len(called) / n * 100
        caught = sum(1 for _, b in called if b)
        recall = caught / n_err * 100 if n_err else 0.0
        precision = caught / len(called) * 100 if called else 0.0
        print(f"{t:>7.1f} {rate:>8.1f}% {recall:>7.1f}% {precision:>9.1f}% "
              f"{n_err - caught:>8}")
        if (rate, recall) not in seen:      # score rời rạc → nhiều ngưỡng trùng kết quả
            seen.add((rate, recall))
            points.append((rate, recall))
            labels.append(t)

    if len(points) > 1:
        # Chỉ xét các ngưỡng có lợi ích thực sự — bỏ điểm (0,0) / recall=0
        useful = [p for p in points if p[0] > 0 and p[1] > 0]
        if len(useful) > 1:
            kx, ky = knee(useful)
            t = labels[points.index((kx, ky))]
            print(f"\nKnee: LLM_CALL_THRESHOLD={t} "
                  f"(gọi LLM {kx:.1f}%, recall lỗi {ky:.1f}%)")
            after = [(l, p) for l, p in zip(labels, points)
                     if p[0] > kx and p[1] > 0]
            if after:
                nt, (nx, ny) = after[0]
                print(f"Bước kế ({nt}): gọi {nx:.1f}% ({nx / kx:.1f}× chi phí) "
                      f"để recall {ny:.1f}% (+{ny - ky:.1f} điểm)")
        else:
            print("\nKhông đủ ngưỡng có lợi ích để tính knee (thử thêm ngưỡng khác).")
    else:
        print("\nKhông đủ điểm để tính knee.")

    print("\nLỗi theo route trích xuất (field | source | confidence → tỉ lệ sai):")
    print(f"{'field':>7} {'source':>6} {'conf':>5} {'n':>5} {'sai':>7}")
    for key in sorted(err_by_field):
        cnt, bad = err_by_field[key]
        print(f"{key[0]:>7} {key[1]:>6} {key[2]:>5} {cnt:>5} {bad / cnt * 100:>6.1f}%")


if __name__ == "__main__":
    main()
