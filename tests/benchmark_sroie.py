"""Benchmark SROIE (ICDAR 2019): 987 hóa đơn/receipt scan THẬT.

Dữ liệu: OCR words + bboxes + ground truth (company/date/total) từ jsdnrs/ICDAR2019-SROIE.
Tái dựng dòng text từ bboxes (như file txt gốc) → extract_from_text (regex path, không LLM)
→ đo field accuracy vendor/company, issue_date/date, total.

Chạy: python tests/benchmark_sroie.py > tests/sroie_baseline.txt
      (sau khi gia cố: > tests/sroie_improved.txt)
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract.extractor import extract_from_text, _normalize_date

DATA = Path(__file__).parent.parent / "data" / "sroie"

_NOISE = re.compile(
    r"^(receipt|tax invoice|gst|abn|acn|phone|tel|fax|www|http|email|date|time|cashier|"
    r"no\.?|item|qty|price|amount|total|subtotal|sub total|grand total|balance|due|"
    r"cash|change|change due|round|paid|tendered|sales|net|subtotal :)$", re.I)


def reconstruct_lines(words, bboxes, height):
    """Tái dựng dòng từ bboxes: gom token cùng y (tolerance theo chiều cao ảnh) → sort theo x."""
    tol = max(6, height * 0.015)
    tokens = sorted(zip(words, bboxes), key=lambda t: (t[1][1], t[1][0]))
    lines = []
    for word, box in tokens:
        y1 = box[1]
        if lines and abs(y1 - lines[-1][0]) <= tol:
            lines[-1][1].append((box[0], word))  # x, word
        else:
            lines.append([y1, [(box[0], word)]])
    return [" ".join(w for _, w in sorted(tok)) for _, tok in lines]


def norm_company(s: str) -> str:
    """Chuẩn hóa tên công ty: uppercase, BỎ HẾT khoảng trắng + dấu tiếng Việt,
    bỏ ký tự lề và số đăng ký Malaysia (SSM/GST) dạng (728515-N), (126926-H), (308282-A).
    Số đăng ký kết thúc bằng CHỮ CÁI → strip bất cứ đâu (không chỉ cuối dòng);
    "(M)" (Malaysia) không bị nhầm vì không có chữ số.
    So sánh theo chuỗi chữ thuần (OCR hay lệch space/dấu) — áp cả 2 vế."""
    if not s:
        return ""
    s = re.sub(r"\s*\([0-9]{4,6}-[A-Za-z]\)", "", s)  # mã đăng ký: 4-6 số + chữ
    s = re.sub(r"\s*\(\s*[\dA-Z-]*-?[\d]+\s*\)\s*$", "", s.strip())
    s = re.sub(r"\s+", "", "".join(
        c for c in unicodedata.normalize("NFD", s.strip(" .,;:/\\\"'()-"))
        if not unicodedata.combining(c)))
    return s.upper()


def norm_total(s) -> float:
    """GT total → float (bỏ $, dấu phân cách)."""
    if s is None:
        return None
    s = str(s).replace("$", "").replace(" ", "")
    try:
        return float(s.replace(",", ""))
    except ValueError:
        try:
            return float(s.replace(",", "").replace(".", ""))
        except ValueError:
            return None


def main():
    recs = []
    for split in ("train", "test"):
        for line in (DATA / f"{split}.jsonl").open(encoding="utf-8"):
            recs.append(json.loads(line))
    print(f"SROIE benchmark: {len(recs)} receipt thật (train 626 + test 361), "
          f"regex path, không LLM\n")

    stats = {"vendor": [0, 0], "date": [0, 0], "total": [0, 0]}
    fails = []

    for r in recs:
        text = "\n".join(reconstruct_lines(r["words"], r["bboxes"], r["image_size"]["height"]))
        try:
            inv = extract_from_text(text, llm=None)
        except Exception as e:  # extractor crash trên dữ liệu thật → tính là fail cả 3 field
            ent = r.get("entities") or {}
            for field, gt in (("total", ent.get("total")), ("vendor", ent.get("company")), ("date", ent.get("date"))):
                if gt:
                    stats[field][1] += 1
                    fails.append((field, r, gt, f"CRASH: {type(e).__name__}", text))
            continue
        ent = r.get("entities") or {}

        # --- total ---
        gt_total = norm_total(ent.get("total"))
        if gt_total is not None:
            stats["total"][1] += 1
            if abs(inv.total - gt_total) < 0.01:
                stats["total"][0] += 1
            else:
                fails.append(("total", r, gt_total, inv.total, text))

        # --- vendor/company ---
        gt_company = norm_company(ent.get("company"))
        if gt_company:
            stats["vendor"][1] += 1
            if norm_company(inv.vendor) == gt_company:
                stats["vendor"][0] += 1
            else:
                fails.append(("vendor", r, gt_company, inv.vendor, text))

        # --- date ---
        gt_date = ent.get("date")
        if gt_date:
            try:
                gt_norm = _normalize_date(gt_date)
            except Exception:
                continue
            stats["date"][1] += 1
            if inv.issue_date == gt_norm:
                stats["date"][0] += 1
            else:
                fails.append(("date", r, gt_norm, inv.issue_date, text))

    total_denom = sum(v[1] for v in stats.values())
    total_ok = sum(v[0] for v in stats.values())
    print(f"{'field':<8}{'matched':>8}{'total':>8}{'accuracy':>10}")
    for k, (ok, den) in stats.items():
        print(f"{k:<8}{ok:>8}{den:>8}{ok / den * 100:>9.1f}%")
    print(f"{'overall':<8}{total_ok:>8}{total_denom:>8}{total_ok / total_denom * 100:>9.1f}%")

    print(f"\n--- {min(len(fails), 40)} mẫu fail đầu tiên (field | expected | got) ---")
    for field, r, exp, got, _ in fails[:40]:
        print(f"{field:<7} {r['key']}  exp={exp!r}  got={got!r}")

    # dump fail chi tiết để phân tích lỗi
    if fails:
        dump = Path(__file__).parent / "sroie_fails.txt"
        with dump.open("w", encoding="utf-8") as f:
            for field, r, exp, got, text in fails:
                f.write(f"=== {r['key']} [{field}] exp={exp!r} got={got!r} ===\n{text}\n\n")
        print(f"\nFail chi tiết: {dump}")


if __name__ == "__main__":
    main()
