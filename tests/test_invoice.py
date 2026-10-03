"""Tests cho Invoice & Billing System."""
import json
import tempfile
from pathlib import Path

from src.domain.models import Invoice, InvoiceStatus, InvoiceUpdate
from src.extract.extractor import OcrLine, extract_from_text, extract_invoice
from src.store.repository import InvoiceRepository


SAMPLE_INVOICE = """INVOICE
Invoice No: INV-2024-001
Vendor: TechCorp Ltd
Invoice Date: 2024-06-15
Due Date: 2024-07-15
Subtotal: $950.00
Tax: $50.00
Total: $1,000.00
"""


def test_extract_fields():
    """Trích xuất các field từ text hóa đơn."""
    inv = extract_from_text(SAMPLE_INVOICE)
    assert inv.invoice_number == "INV-2024-001"
    assert inv.vendor == "TechCorp Ltd"
    assert inv.issue_date == "2024-06-15"
    assert inv.due_date == "2024-07-15"
    assert inv.total == 1000.0


def test_extract_file():
    """Trích xuất từ file content (text/plain)."""
    content = SAMPLE_INVOICE.encode("utf-8")
    inv = extract_invoice(content=content, mime_type="text/plain", source_file="test.txt")
    assert inv.invoice_number == "INV-2024-001"
    assert inv.vendor == "TechCorp Ltd"
    assert inv.total == 1000.0


def test_repository_upsert_and_get():
    """Lưu và lấy hóa đơn."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    fetched = repo.get(inv.id, "u1")
    assert fetched is not None
    assert fetched.total == 1000.0
    assert fetched.status == InvoiceStatus.UNPAID


def test_repository_update_status():
    """Cập nhật status hóa đơn."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    updated = repo.update(inv.id, InvoiceUpdate(status=InvoiceStatus.PAID), user_id="u1")
    assert updated is not None
    assert updated.status == InvoiceStatus.PAID


def test_repository_list_filter():
    """Liệt kê và lọc hóa đơn."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    all_inv = repo.list(user_id="u1")
    assert len(all_inv) == 1
    paid = repo.list(user_id="u1", status=InvoiceStatus.PAID)
    assert len(paid) == 0
    unpaid = repo.list(user_id="u1", status=InvoiceStatus.UNPAID)
    assert len(unpaid) == 1


def test_multi_user_isolation():
    """Isolation giữa các user."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    # User 2 không thấy invoice của user 1
    assert repo.get(inv.id, "u2") is None
    assert len(repo.list(user_id="u2")) == 0


def test_monthly_report():
    """Báo cáo theo tháng."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    report = repo.monthly_report("2024-06", user_id="u1")
    assert report is not None
    assert report.invoice_count == 1
    assert report.total_amount == 1000.0


def test_monthly_report_empty_month():
    """Báo cáo tháng không có hóa đơn."""
    repo = InvoiceRepository(db_path=":memory:")
    report = repo.monthly_report("2024-01", user_id="u1")
    assert report is None


def test_delete_invoice():
    """Xóa hóa đơn."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    assert repo.delete(inv.id, user_id="u1") is True
    assert repo.get(inv.id, "u1") is None
    # Xóa lại phải False
    assert repo.delete(inv.id, user_id="u1") is False


# --- PDF quét (scan) fallback OCR + ngôn ngữ OCR ---


def _blank_pdf() -> bytes:
    """PDF 1 trang trắng, không text layer (giống PDF scan)."""
    import io as _io
    from reportlab.pdfgen import canvas

    buf = _io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_scanned_pdf_falls_back_to_ocr(monkeypatch):
    """PDF không có text layer → render trang + OCR thay vì trả text rỗng."""
    import src.extract.extractor as ext

    calls = []
    monkeypatch.setattr(
        ext, "_paddle_ocr_lines",
        lambda b: (calls.append(b), [ext.OcrLine("GD -350.000 VND", (0, 0, 100, 20))])[1],
    )
    text = ext._extract_pdf_text(_blank_pdf())
    assert text == "GD -350.000 VND"
    assert len(calls) == 1
    assert calls[0][:8] == b"\x89PNG\r\n\x1a\n"  # ảnh render ra là PNG


def test_pdf_with_text_layer_skips_ocr(monkeypatch):
    """PDF có text layer bình thường → không đụng đến OCR."""
    import io as _io
    from reportlab.pdfgen import canvas
    import src.extract.extractor as ext

    buf = _io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "TOTAL: 1,000.00")
    c.save()

    monkeypatch.setattr(
        ext, "_paddle_ocr_lines",
        lambda b: (_ for _ in ()).throw(AssertionError("OCR không được gọi")),
    )
    assert "TOTAL: 1,000.00" in ext._extract_pdf_text(buf.getvalue())


def test_ocr_uses_vietnamese_model(monkeypatch):
    """OCR production phải dùng lang=vi (khớp benchmark, giữ dấu tiếng Việt)."""
    import sys
    import types
    import io as _io
    from PIL import Image
    import src.extract.extractor as ext

    captured = {}

    class FakePaddle:
        def __init__(self, **kw):
            captured.update(kw)

        def ocr(self, arr, cls=True):
            # format thật: [detections] → detection = [box 4 điểm, (text, conf)]
            box = [[0, 0], [10, 0], [10, 10], [0, 10]]
            return [[[box, ("Hoa don 350.000", 0.99)]]]

    fake = types.ModuleType("paddleocr")
    fake.PaddleOCR = FakePaddle
    monkeypatch.setitem(sys.modules, "paddleocr", fake)

    buf = _io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    assert ext._extract_ocr_text(buf.getvalue()) == "Hoa don 350.000"
    assert captured.get("lang") == "vi"


# --- Layout-aware total (bbox từ OCR) ---


def test_layout_total_value_below_label():
    """Fail MCOCR: số nằm DƯỚI dòng 'Tổng cộng' — layout bắt được, keyword thì không."""
    from src.extract.extractor import _pick_total, _pick_total_layout

    layout = [
        OcrLine("Co.opmart", (0, 0, 200, 24)),
        OcrLine("Tổng cộng", (0, 100, 120, 124)),
        OcrLine("350.000", (300, 130, 400, 154)),
    ]
    assert _pick_total_layout(layout) == "350.000"
    assert _pick_total("Co.opmart\nTổng cộng\n350.000") is None  # keyword bỏ sót


def test_layout_skips_subtotal_and_picks_bottom_total():
    """SUB-TOTAL/'tổng cộng tiền hàng' bị loại; cùng nhãn → dòng dưới thắng."""
    from src.extract.extractor import _pick_total_layout

    layout = [
        OcrLine("Tong cong tien hang 300.000", (0, 50, 220, 70)),  # subtotal
        OcrLine("Total 320.000", (0, 120, 140, 140)),               # total giữa receipt
        OcrLine("Tong cong 350.000", (0, 300, 160, 320)),           # dưới cùng — thắng
    ]
    assert _pick_total_layout(layout) == "350.000"


def test_extract_invoice_image_uses_layout_total(monkeypatch):
    """Ảnh OCR end-to-end: layout được thread qua extract_invoice → total đúng."""
    import src.extract.extractor as ext

    monkeypatch.setattr(ext, "_paddle_ocr_lines", lambda b: [
        OcrLine("CO.OPMART", (0, 0, 200, 20)),
        OcrLine("Tổng cộng", (0, 100, 120, 124)),
        OcrLine("350.000", (300, 130, 400, 154)),
    ])
    inv = ext.extract_invoice(b"fake-png", "image/png", "scan.png")
    assert inv.total == 350000.0
    assert inv.currency == "VND"


# --- Parser số đa locale (2 quy ước + ngữ cảnh receipt) ---


def test_to_float_mixed_separators_locale_independent():
    """Token có cả 2 dấu → dấu đứng cuối là thập phân, bất kể cờ locale."""
    from src.extract.extractor import _to_float

    assert _to_float("1,234.56", vi=True) == 1234.56   # bug cũ: vi=True → 1.23456
    assert _to_float("1,234.56", vi=False) == 1234.56
    assert _to_float("1.234,56", vi=False) == 1234.56


def test_to_float_leading_zero_is_decimal():
    """0.xxx là số thập phân, không phải nhóm 3 nghìn (bug cũ: 0.125 → 125)."""
    from src.extract.extractor import _to_float

    assert _to_float("0.125") == 0.125
    assert _to_float("0,125", vi=True) == 0.125
    # nhóm 3 thật vẫn là nghìn
    assert _to_float("61.500", vi=True) == 61500.0
    assert _to_float("1.591.600") == 1591600.0  # nhiều nhóm → luôn nghìn


def test_detect_comma_decimal_by_context():
    from src.extract.extractor import _detect_comma_decimal

    # KR/EU: nhóm 3 bằng dấu chấm → phẩy là thập phân
    assert _detect_comma_decimal("MENU\n61.500\n1.591.600") is True
    # US: token 2 dấu + in 2 số lẻ → chấm là thập phân
    assert _detect_comma_decimal("STORE\n1,234.56\n2.50\n12.99") is False
    # Không đủ bằng chứng → theo ngôn ngữ (VI detect)
    assert _detect_comma_decimal("Tổng cộng tiền thanh toán") is True
    assert _detect_comma_decimal("") is False


def test_extract_total_chooses_convention_by_context():
    """'1.500' trong receipt Mỹ = 1.5 (có token 2 số lẻ chứng minh); '61.500' receipt KR = 61.500 won."""
    from src.extract.extractor import extract_from_text

    us = extract_from_text("STORE\n2.50\n12.99\nTOTAL 1.500")
    assert us.total == 1.5
    kr = extract_from_text("COOP\nSIDE 12.00\nTOTAL 61.500")
    assert kr.total == 61500.0
    eu = extract_from_text("MARKT\nTOTAL 1.234,56")
    assert eu.total == 1234.56
    # Hóa đơn VI nhưng số format US → vẫn đọc đúng
    vi_us = extract_from_text("Tổng cộng tiền thanh toán: 1,234.56")
    assert vi_us.total == 1234.56
