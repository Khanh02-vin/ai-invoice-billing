"""Benchmark: đo độ chính xác trích xuất trên 50 hóa đơn GTGT mẫu chuẩn.

Sinh hóa đơn đa dạng (vendor, số, SL, thuế 10%/8%, chiết khấu, với/không chiết khấu)
rồi chạy extractor (regex path, không cần LLM key) — đo field accuracy.
Chạy: python -m tests.benchmark_gtgt
"""
import random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract.extractor import extract_from_text

random.seed(42)

VENDORS = ["CÔNG TY TNHH ABC", "CTY CP ĐẦU TƯ XYZ", "Công ty TNHH MTV Thương mại Bách Hóa",
           "CTY TNHH SX TM DV Hoàng Long", "CÔNG TY CỔ PHẦN CÔNG NGHỆ ATN"]
ITEMS = [("Máy tính Dell", 20000000), ("Màn hình LG", 4500000), ("Bàn phím cơ", 1500000),
         ("Chuột không dây", 800000), ("Laptop Asus", 25000000), ("Ổ cứng SSD 1TB", 2200000),
         ("Màn hình Samsung 27\"", 5500000), ("Tai nghe", 1200000), ("Webcam FHD", 900000)]


def gen_invoice(i):
    vendor = VENDORS[i % len(VENDORS)]
    num = f"{random.randint(100, 99999):07d}"
    mst = f"0{random.randint(100000000, 999999999)}"
    mst_b = f"0{random.randint(100000000, 999999999)}"
    lines, subtotal = [], 0
    for _ in range(random.randint(1, 4)):
        name, price = random.choice(ITEMS)
        qty = random.randint(1, 5)
        amt = price * qty
        subtotal += amt
        lines.append(f"{name}  {qty}  {price:,}  {amt:,}")
    # có/không chiết khấu
    discount = random.choice([0, 0, round(subtotal * 0.05), round(subtotal * 0.1)])
    sub_after = subtotal - discount
    tax_rate = random.choice([8, 10])
    tax = round(sub_after * tax_rate / 100)
    total = sub_after + tax
    return f"""HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Mẫu số: 01GTKT0/00{i%9+1}
Ký hiệu: 1C26TAA
Số hóa đơn: {num}
Người bán: {vendor}
MST: {mst}
Địa chỉ: Số {i+1} Đường Láng, Đống Đa, Hà Nội
Người mua: CÔNG TY CỔ PHẦN XYZ MAX
MST: {mst_b}
STT Tên hàng hóa, dịch vụ  SL  Đơn giá     Thành tiền
{chr(10).join(f"{k+1}  {l}" for k, l in enumerate(lines))}
Cộng tiền hàng hóa, dịch vụ: {subtotal:,}
Chiết khấu thương mại: {discount:,}
Thuế suất GTGT: {tax_rate}% Thuế GTGT: {tax:,}
Tổng cộng tiền thanh toán: {total:,}
Ngày 0{i%28+1}/08/2026
Người mua hàng (ký)              Người bán hàng (ký)
""", {"invoice_number": num, "vendor": vendor, "total": total, "tax": tax}


def main():
    N = 50
    correct = {"invoice_number": 0, "vendor": 0, "total": 0, "tax": 0}
    for i in range(N):
        text, truth = gen_invoice(i)
        inv = extract_from_text(text)
        if inv.invoice_number == truth["invoice_number"]:
            correct["invoice_number"] += 1
        if (inv.vendor or "").strip().upper() == truth["vendor"].strip().upper():
            correct["vendor"] += 1
        if inv.total == truth["total"]:
            correct["total"] += 1
        if inv.tax == truth["tax"]:
            correct["tax"] += 1
    print(f"Benchmark: {N} hóa đơn GTGT chuẩn (regex path, không LLM)")
    for k, c in correct.items():
        print(f"  {k}: {c}/{N} = {c/N*100:.1f}%")
    overall = sum(correct.values()) / (N * 4)
    print(f"  Overall field accuracy: {overall*100:.1f}%")


if __name__ == "__main__":
    main()