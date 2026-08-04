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

Người mua: CÔNG TY CỔ PHẦN XYZ
MST: 0309876543

STT Tên hàng hóa, dịch vụ  SL  Đơn giá     Thành tiền
1   Máy tính Dell          1   20,000,000  20,000,000
2   Màn hình LG            2   4,500,000   9,000,000

Cộng tiền hàng hóa, dịch vụ: 29,000,000
Chiết khấu thương mại: 0
Thuế suất GTGT: 10% Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 31,900,000
Số tiền bằng chữ: Ba mươi mốt triệu chín trăm nghìn đồng

Ngày 04/08/2026
Người mua hàng (ký)              Người bán hàng (ký)
"""


def test_gtgt_all_fields():
    """Trích xuất đủ trường hóa đơn GTGT."""
    inv = extract_from_text(GTGT_INVOICE)
    assert inv.invoice_number == "00012345"
    assert inv.vendor == "CÔNG TY TNHH ABC"
    assert inv.currency == "VND"
    assert inv.confidence == 1.0


def test_gtgt_total_not_subtotal():
    """Tổng phải là 31,900,000 (có thuế), không phải 29,000,000 (subtotal)."""
    inv = extract_from_text(GTGT_INVOICE)
    assert inv.total == 31900000.0
    assert inv.total != 29000000.0


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
    """'Số hoá đơn' (hoá thay hóa)."""
    inv = extract_from_text(GTGT_INVOICE.replace("hóa đơn", "hoá đơn"))
    assert inv.invoice_number == "00012345"


def test_english_invoice_still_works():
    """Hóa đơn tiếng Anh không bị phá vỡ."""
    inv = extract_from_text(
        """INVOICE
Invoice No: INV-2024-001
Vendor: TechCorp Ltd
Invoice Date: 2024-06-15
Due Date: 2024-07-15
Tax: $50.00
Total: $1,000.00
Currency: USD
"""
    )
    assert inv.invoice_number == "INV-2024-001"
    assert inv.total == 1000.0
    assert inv.tax == 50.0
    assert inv.currency == "USD"
    assert inv.due_date == "2024-07-15"


MULTI_TAX_INVOICE = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00099999
Người bán: CÔNG TY TNHH NHIỀU THUẾ

Cộng tiền hàng hóa, dịch vụ: 30,000,000
Thuế suất: 10% Thuế GTGT: 2,900,000
Thuế suất: 8%  Thuế GTGT: 2,320,000
Tổng cộng tiền thanh toán: 35,220,000
Ngày 10/07/2026
"""


def test_multi_tax_summed():
    """Nhiều mức thuế (10% + 8%) cộng dồn."""
    inv = extract_from_text(MULTI_TAX_INVOICE)
    assert inv.tax == 5220000.0  # 2,900,000 + 2,320,000


def test_tax_not_rate():
    """Thuế suất % không bị đọc thành thuế."""
    inv = extract_from_text(MULTI_TAX_INVOICE)
    assert inv.tax != 10.0
    assert inv.tax != 8.0


DISCOUNT_INVOICE = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00088888
Người bán: CÔNG TY TNHH GIẢM GIÁ

Cộng tiền hàng hóa, dịch vụ: 30,000,000
Chiết khấu thương mại: 1,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 31,900,000
Ngày 15/07/2026
"""


def test_discount_extracted():
    """Chiết khấu được trích xuất riêng."""
    inv = extract_from_text(DISCOUNT_INVOICE)
    assert inv.discount == 1000000.0


def test_discount_percent_not_captured():
    """Chiết khấu dạng % không bị đọc."""
    inv = extract_from_text(
        """INVOICE
Invoice No: INV-DISC-1
Total: $900.00
Discount: 10%
Tax: $90.00
"""
    )
    assert inv.discount == 0.0


def test_discount_zero_default():
    """Không có chiết khấu → 0."""
    inv = extract_from_text(MULTI_TAX_INVOICE)
    assert inv.discount == 0.0


def test_ocr_paddle_then_tesseract():
    """OCR ảnh: Paddle fail → Tesseract lấy."""
    with (mock.patch.object(extractor, "_try_paddle", return_value=""),
          mock.patch.object(extractor, "_try_tesseract",
                            return_value="Invoice No: INV-IMG-1\nTotal: $50.00\nVendor: Scan Co")):
        text = extractor.read_file_text("bill.png")
    inv = extract_from_text(text)
    assert inv.invoice_number == "INV-IMG-1"
    assert inv.total == 50.0


def test_ocr_unavailable_graceful():
    """Không có engine OCR → không crash, trả về unknown."""
    with (mock.patch.object(extractor, "_try_paddle", return_value=""),
          mock.patch.object(extractor, "_try_tesseract", return_value="")):
        text = extractor.read_file_text("bill.jpg")
    assert text == ""
    inv = extract_from_text(text)
    assert inv.invoice_number == "unknown"
    assert inv.confidence == 0.0