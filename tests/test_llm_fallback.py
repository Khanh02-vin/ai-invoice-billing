"""Tests cho LLM fallback — regex fail → LLM lấp chỗ thiếu."""
from src.extract.extractor import extract_from_text, _merge_fields
from src.llm.base import MockProvider


ODD_INVOICE = """HÓA ĐƠN THƯƠNG MẠI
Mã: HD-TM-2026-77
Đơn vị bán: CÔNG TY XUẤT NHẬP KHẨU VIỆT TIẾN
Ngày lập: 15/08/2026

Mặt hàng: 200 thùng cà phê
Giá trị thanh toán: 85.500.000
Thuế: 8.550.000
Tổng phải trả: 94.050.000
"""


def test_llm_fallback_fills_missing():
    """Regex confidence thấp (0.2) → LLM lấp đầy → confidence 1.0."""
    llm_json = {
        "invoice_number": "HD-TM-2026-77",
        "vendor": "CÔNG TY XUẤT NHẬP KHẨU VIỆT TIẾN",
        "issue_date": "2026-08-15",
        "total": "94,050,000",
        "tax": "8,550,000",
        "discount": 0,
        "currency": "VND",
    }
    inv = extract_from_text(ODD_INVOICE, llm=MockProvider(str(llm_json).replace("'", '"')))
    assert inv.invoice_number == "HD-TM-2026-77"
    assert inv.total == 94050000.0
    assert inv.tax == 8550000.0
    assert inv.confidence == 1.0


def test_llm_json_with_backticks():
    """LLM trả JSON trong ``` markdown → vẫn parse được."""
    response = '```json\n{"invoice_number": "ABC-1", "total": "123.45", "currency": "USD"}\n```'
    inv = extract_from_text("FOO BAR BAZ", llm=MockProvider(response))
    assert inv.invoice_number == "ABC-1"
    assert inv.total == 123.45


def test_llm_not_called_when_confident():
    """Regex đọc tốt (confidence ≥ 0.8) → KHÔNG gọi LLM, giữ giá trị regex."""
    class ExplodingProvider:
        def complete(self, system, user):
            raise AssertionError("LLM không được gọi khi regex đủ tốt")

    inv = extract_from_text(
        """INVOICE
Invoice No: INV-2024-001
Vendor: TechCorp Ltd
Invoice Date: 2024-06-15
Tax: $50.00
Total: $1,000.00
Currency: USD
""",
        llm=ExplodingProvider(),
    )
    assert inv.invoice_number == "INV-2024-001"
    assert inv.total == 1000.0


def test_llm_failure_graceful():
    """LLM trả garbage → không crash, giữ kết quả regex."""
    inv = extract_from_text(ODD_INVOICE, llm=MockProvider("không phải json"))
    assert inv.invoice_number == "unknown"  # regex không đọc được, LLM hỏng → unknown


def test_merge_regex_wins_for_conflict():
    """Regex là nguồn chính — LLM không đè trường regex đã có."""
    merged = _merge_fields(
        {"invoice_number": "INV-OK-1", "vendor": "unknown", "total": 100.0, "tax": 0.0, "discount": 0.0},
        {"invoice_number": "INV-LLM-9", "vendor": "LLM Vendor", "total": 999.0, "tax": 50.0, "discount": 0.0},
    )
    assert merged["invoice_number"] == "INV-OK-1"  # regex giữ
    assert merged["vendor"] == "LLM Vendor"  # LLM lấp chỗ unknown
    assert merged["total"] == 100.0  # regex giữ
    assert merged["tax"] == 50.0  # LLM lấp chỗ 0