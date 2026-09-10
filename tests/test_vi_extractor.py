"""Tests cho trích xuất hóa đơn GTGT điện tử tiếng Việt + OCR + nhiều thuế/chiết khấu."""
from unittest import mock

from src.extract import extractor
from src.extract.extractor import extract_from_text

GTGT_INVOICE = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Mẫu số: 01GTKT0/001
Ký hiệu: 1C26TAA
Số hóa đơn: 00012345

Người bán: CÔNG TY TNHH ABC
MST: 0101234567
Địa chỉ: Số 1 Đường Láng, Đống Đa, Hà Nội

Cộng tiền hàng hóa, dịch vụ: 29,000,000
Chiết khấu thương mại: 1,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 30,900,000
Ngày 04/08/2026
"""


def test_gtgt_all_fields():
    """Trích xuất đầy đủ field từ GTGT."""
    inv = extract_from_text(GTGT_INVOICE)
    assert inv.invoice_number == "00012345"
    assert inv.vendor == "CÔNG TY TNHH ABC"
    assert inv.total == 30900000.0
    assert inv.tax == 2900000.0
    assert inv.currency == "VND"


def test_gtgt_tax_not_rate():
    """Thuế là 2,900,000 (số tiền), không phải 10% (thuế suất)."""
    inv = extract_from_text(GTGT_INVOICE)
    assert inv.tax == 2900000.0
    assert inv.tax != 10.0


def test_gtgt_date():
    """Ngày Việt Nam dd/mm/yyyy → chuẩn hóa thành ISO yyyy-mm-dd."""
    inv = extract_from_text(GTGT_INVOICE)
    assert inv.issue_date == "2026-08-04"


def test_gtgt_hoa_spelling_variant():
    """'Số hoá đơn' (hoá thay hoa) vẫn nhận ra."""
    text = GTGT_INVOICE.replace("Số hóa đơn", "Số hoá đơn")
    inv = extract_from_text(text)
    assert inv.invoice_number == "00012345"


def test_english_invoice_still_works():
    """Hóa đơn tiếng Anh vẫn trích xuất được."""
    text = """INVOICE
Invoice No: INV-2024-001
Vendor: TechCorp Ltd
Invoice Date: 2024-06-15
Due Date: 2024-07-15
Tax: $50.00
Total: $1,000.00
"""
    inv = extract_from_text(text)
    assert inv.invoice_number == "INV-2024-001"
    assert inv.vendor == "TechCorp Ltd"
    assert inv.total == 1000.0


def test_multi_tax_summed():
    """Nhiều dòng thuế → tổng."""
    text = """HÓA ĐƠN
Số: 001
Người bán: ABC
Thuế GTGT: 1,000,000
Thuế tiêu thụ đặc biệt: 500,000
Tổng cộng: 10,000,000
"""
    inv = extract_from_text(text)
    # Thuế được tổng (1,000,000 + 500,000) = 1,500,000
    assert inv.tax == 1500000.0
