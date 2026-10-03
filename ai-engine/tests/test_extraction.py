from invoice_ai import extract_from_text


def test_english_invoice_contract():
    invoice = extract_from_text(
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
    assert invoice.invoice_number == "INV-2024-001"
    assert invoice.vendor == "TechCorp Ltd"
    assert invoice.total == 1000.0
    assert invoice.tax == 50.0
    assert invoice.total == 1000.0


def test_vietnamese_invoice_contract():
    invoice = extract_from_text(
        """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00012345
Người bán: CÔNG TY TNHH ABC
Ngày lập: 15/08/2026
Cộng tiền hàng hóa, dịch vụ: 29,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 31,900,000
"""
    )
    assert invoice.invoice_number == "00012345"
    assert "ABC" in invoice.vendor
    assert invoice.total == 31900000.0


def test_no_llm_key_keeps_extraction_offline(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    invoice = extract_from_text("Vendor: Offline Shop\nTotal: 12.50")
    assert invoice.vendor == "Offline Shop"
    assert invoice.total == 12.5
