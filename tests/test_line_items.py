"""Trích line items từ bảng hàng hóa (Mã SP / SL / Đơn giá / Thành tiền)."""
from src.extract.extractor import OcrLine, extract_from_text
from src.store.repository import InvoiceRepository


VN_SAMPLE = """CÔNG TY TNHH ABC
HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Mã SP | Tên hàng | SL | Đơn giá | Thành tiền
Bánh mì thịt nướng | 2 | 35.000 | 70.000
Cà phê sữa đá | 3 | 25.000 | 75.000
Trà đá | 1 | 5.000 | 5.000
Tổng cộng tiền thanh toán: 150.000
"""

EN_SAMPLE = """ACME STORE
ITEM QTY PRICE AMOUNT
Coffee Beans 2 12.50 25.00
Green Tea 1 8.00 8.00
SUBTOTAL 33.00
TAX 3.30
TOTAL 36.30
"""


def test_line_items_vn_table():
    """Bảng VN có cột Mã SP/SL/Đơn giá/Thành tiền → 3 dòng hàng, đúng số."""
    inv = extract_from_text(VN_SAMPLE)
    assert len(inv.line_items) == 3
    it = inv.line_items[0]
    assert it.description == "Bánh mì thịt nướng"
    assert it.quantity == 2
    assert it.unit_price == 35000
    assert it.amount == 70000
    assert inv.line_items[1].amount == 75000
    assert inv.line_items[2].description == "Trà đá"
    # line_items KHÔNG đụng field khác
    assert inv.total == 150000
    # provenance chỉ có field lõi — line_items không tự thêm entry
    assert "line_items" not in inv.provenance
    assert {"total", "vendor"} <= set(inv.provenance)


def test_line_items_en_table():
    """Receipt Anh (ITEM/QTY/PRICE/AMOUNT) → dừng đúng ở SUBTOTAL."""
    inv = extract_from_text(EN_SAMPLE)
    assert len(inv.line_items) == 2
    it = inv.line_items[0]
    assert it.description == "Coffee Beans"
    assert it.quantity == 2
    assert it.unit_price == 12.5
    assert it.amount == 25.0


def test_line_items_layout_path():
    """Cùng bảng nhưng đi qua layout (OCR bbox) → vẫn bắt được."""
    layout = [
        OcrLine(l, (0.0, i * 20.0, 400.0, i * 20.0 + 18.0))
        for i, l in enumerate(VN_SAMPLE.splitlines())
    ]
    inv = extract_from_text(VN_SAMPLE, layout=layout)
    assert len(inv.line_items) == 3


def test_line_items_rejects_junk_numbers():
    """Dòng không thỏa SL × Đơn giá ≈ Thành tiền (sđt, ngày...) → không lấy."""
    text = (
        "Mã SP Tên hàng SL Đơn giá Thành tiền\n"
        "Quà tặng 5 7 99\n"            # 5×7=35 ≠ 99
        "Liên hệ 0912345678 2026 999\n"  # SL quá lớn
        "Gì đó 2 30.000 60.000\n"
    )
    inv = extract_from_text(text)
    assert [i.description for i in inv.line_items] == ["Gì đó"]


def test_line_items_no_header_no_items():
    """Không có dòng header bảng → không bịa item."""
    inv = extract_from_text("Invoice No X\nVendor: ABC\nTotal: 100.000")
    assert inv.line_items == []


def test_line_items_persist_roundtrip():
    """Lưu repo → lấy lại vẫn còn đủ dòng hàng (JSON serialize)."""
    repo = InvoiceRepository(":memory:")
    inv = extract_from_text(VN_SAMPLE)
    inv.user_id = "u1"
    repo.upsert(inv)
    got = repo.get(inv.id, "u1")
    assert got is not None
    assert len(got.line_items) == 3
    assert got.line_items[0].amount == 70000
