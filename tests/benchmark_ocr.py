"""Benchmark OCR path: hóa đơn GTGT render thành ảnh → OCR → extract.

Mô phỏng ảnh scan thật: render bằng PIL (font Việt), chạy extract_invoice
(OCR chain PaddleOCR → regex). Chạy: python tests/benchmark_ocr.py
"""
import random
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image, ImageDraw, ImageFont
from src.extract.extractor import extract_invoice
from tests.benchmark_gtgt import gen_invoice

random.seed(7)

FONT_PATH = r"C:\Windows\Fonts\arial.ttf"


def render_invoice(text: str, out: str, scale: int = 2):
    """Render text thành ảnh trắng-đen như ảnh scan (font 11px, scale 2x)."""
    font = ImageFont.truetype(FONT_PATH, 11)
    # đo kích thước
    lines = text.split("\n")
    w = max(font.getbbox(l)[2] for l in lines) + 40
    h = len(lines) * 16 + 40
    img = Image.new("RGB", (w * scale, h * scale), "white")
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((20 * scale, (20 + i * 16) * scale), ln, fill="black", font=font)
    img.save(out)


def main():
    N = 10
    ok_total = ok_num = 0
    with tempfile.TemporaryDirectory() as td:
        for i in range(N):
            text, truth = gen_invoice(i)
            img_path = str(Path(td) / f"inv_{i}.png")
            render_invoice(text, img_path)
            inv = extract_invoice(img_path)  # OCR chain thật
            if inv.total == truth["total"]:
                ok_total += 1
            if inv.invoice_number == truth["invoice_number"]:
                ok_num += 1
            t_ok = "OK" if inv.total == truth["total"] else "FAIL %s != %s" % (inv.total, truth["total"])
            n_ok = "OK" if inv.invoice_number == truth["invoice_number"] else "FAIL"
            print(f"  #{i}: total {t_ok} | num {n_ok}")
    print(f"OCR benchmark: {N} ảnh hóa đơn GTGT")
    print(f"  invoice_number: {ok_num}/{N} = {ok_num/N*100:.0f}%")
    print(f"  total: {ok_total}/{N} = {ok_total/N*100:.0f}%")


if __name__ == "__main__":
    main()