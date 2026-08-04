"""Demo end-to-end: trích xuất hóa đơn (Anh + Việt GTGT) → lưu → báo cáo."""
from pathlib import Path

from src.domain.models import InvoiceStatus, InvoiceUpdate
from src.extract.extractor import extract_from_text
from src.store.repository import InvoiceRepository

INVOICES = [
    # Hóa đơn tiếng Anh
    """INVOICE
Invoice No: INV-2024-001
Vendor: TechCorp Ltd
Invoice Date: 2024-06-15
Due Date: 2024-07-15
Tax: $50.00
Total: $1,000.00
Currency: USD
""",
    # Hóa đơn GTGT điện tử tiếng Việt
    """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Mẫu số: 01GTKT0/001
Ký hiệu: 1C26TAA
Số hóa đơn: 00012345

Người bán: CÔNG TY TNHH ABC
MST: 0101234567

Người mua: CÔNG TY CỔ PHẦN XYZ

1  Máy tính Dell    20,000,000
2  Màn hình LG      9,000,000
--------------------------------
Cộng tiền hàng hóa, dịch vụ: 29,000,000
Thuế suất GTGT: 10% Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 31,900,000

Ngày 04/08/2026
""",
    # Hóa đơn GTGT: 2 mức thuế + chiết khấu
    """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00099999
Người bán: CÔNG TY TNHH NHIỀU THUẾ

Cộng tiền hàng hóa, dịch vụ: 30,000,000
Chiết khấu thương mại: 1,000,000
Thuế suất: 10% Thuế GTGT: 2,900,000
Thuế suất: 8%  Thuế GTGT: 2,320,000
Tổng cộng tiền thanh toán: 34,220,000

Ngày 10/08/2026
""",
]

repo = InvoiceRepository(db_path="invoices_demo.db")

print("=" * 64)
print("INVOICE & BILLING SYSTEM - DEMO")
print("=" * 64)

# 1. Trích xuất + lưu
print("\n[1] Trích xuất hóa đơn:")
for text in INVOICES:
    inv = extract_from_text(text, source_file="demo")
    repo.upsert(inv)
    print(f"  - {inv.invoice_number:<12} | {inv.vendor:<18} | {inv.currency} "
          f"{inv.total:>14,.0f} | tax {inv.tax:>12,.0f} | disc {inv.discount:>10,.0f} | conf={inv.confidence:.2f}")

# 2. Liệt kê hóa đơn chưa thanh toán
print("\n[2] Hóa đơn chưa thanh toán:")
for inv in repo.list(status=InvoiceStatus.UNPAID):
    print(f"  - {inv.invoice_number}: {inv.currency} {inv.total:,.0f} ({inv.vendor})")

# 3. Đánh dấu hóa đơn tiếng Anh là paid
print("\n[3] Đánh dấu INV-2024-001 là PAID:")
inv1 = next(i for i in repo.list() if i.invoice_number == "INV-2024-001")
updated = repo.update(inv1.id, InvoiceUpdate(status=InvoiceStatus.PAID))
print(f"  - {updated.invoice_number}: {updated.status.value}")

# 4. Báo cáo tháng (cả 2 hóa đơn cùng kỳ)
print("\n[4] Báo cáo tháng 2024-06 (hóa đơn Anh):")
report = repo.monthly_report("2024-06")
if report:
    print(f"  - Số hóa đơn: {report.invoice_count} | Tổng: ${report.total_amount:,.2f}")
    print(f"  - Đã thanh toán: ${report.paid_amount:,.2f} | Chưa: ${report.unpaid_amount:,.2f}")

print("\n[5] Báo cáo tháng 2026-08 (hóa đơn GTGT Việt):")
report = repo.monthly_report("2026-08")
if report:
    print(f"  - Số hóa đơn: {report.invoice_count} | Tổng: {report.total_amount:,.0f} VND | "
          f"Thuế: {report.total_tax:,.0f} VND")

print("\n" + "=" * 64)
print("DEMO HOÀN TẤT")

# dọn demo db
Path("invoices_demo.db").unlink()