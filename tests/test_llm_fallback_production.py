"""Regression tests — LLM fallback trong đường ĐÚC PRODUCTION (không chỉ helper).

Bug gốc (sprint 1): `extract_invoice` gọi `extract_from_text(...)` KHÔNG truyền
`llm=`, nên nhánh LLM fallback ở `src/extract/extractor.py` là dead code từ mọi
đường thực tế (worker upload, camera upload, IMAP poller, batch). Các test
`tests/test_llm_fallback.py` cũ không bắt được vì chúng tự inject `llm=`.

Toàn bộ test dưới đây đi qua entrypoint thật `src.extract.extractor.extract_invoice`
— chính hàm worker/API gọi — và chỉ patch nguồn cấp provider
(`src.extract.extractor.get_llm_provider`), đúng pattern monkeypatch đã có trong
tests/test_payments_api.py.
"""
import os

import src.extract.extractor as ext
from src.extract.extractor import extract_invoice, extract_from_text
from src.llm.base import MockProvider

# Hóa đơn chuẩn — regex đọc đủ mọi field → confidence ≥ 0.8 → KHÔNG gọi LLM.
SAMPLE_INVOICE = """INVOICE
Invoice No: INV-2024-001
Vendor: TechCorp Ltd
Invoice Date: 2024-06-15
Due Date: 2024-07-15
Subtotal: $950.00
Tax: $50.00
Total: $1,000.00
Currency: USD
"""

# Hóa đơn VI "lạ" — regex chỉ bắt được 1/5 field → confidence 0.2 < 0.7 → gọi LLM.
ODD_INVOICE = """HÓA ĐƠN THƯƠNG MẠI
Mã: HD-TM-2026-77
Đơn vị bán: CÔNG TY XUẤT NHẬP KHẨU VIỆT TIẾN
Ngày lập: 15/08/2026

Mặt hàng: 200 thùng cà phê
Giá trị thanh toán: 85.500.000
Thuế: 8.550.000
Tổng phải trả: 94.050.000
"""

ODD_LLM_JSON = (
    '{"invoice_number": "HD-TM-2026-77", '
    '"vendor": "CÔNG TY XUẤT NHẬP KHẨU VIỆT TIẾN", '
    '"issue_date": "2026-08-15", "total": "94.050.000", '
    '"tax": "8.550.000", "currency": "VND"}'
)


class CountingProvider:
    """Provider ghi nhận số lần gọi — dùng chứng minh provider thật sự được truyền."""

    def __init__(self, response: str = ODD_LLM_JSON):
        self.response = response
        self.calls = 0
        self.prompt = ""

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.prompt = user
        return self.response


class ExplodingProvider:
    def complete(self, system, user):
        raise AssertionError("LLM không được gọi trên đường production")


def _patch(monkeypatch, provider):
    monkeypatch.setattr(ext, "get_llm_provider", lambda: provider)


def test_production_entrypoint_passes_llm(monkeypatch):
    """(a) Entrypoint thật extract_invoice RESOLVE và TRUYỀN llm cho fallback.

    Đây là test bắt bug gốc: trước fix provider không được gọi lần nào
    (llm=None toàn tuyến) và total trả về 0.0.
    """
    provider = CountingProvider()
    _patch(monkeypatch, provider)

    inv = extract_invoice(ODD_INVOICE.encode("utf-8"), "text/plain", "odd.txt")

    assert provider.calls == 1, "extract_invoice không truyền provider cho fallback"
    assert provider.prompt.strip().startswith("HÓA ĐƠN")
    # Fill mode: regex giữ vendor/date; LLM lấp invoice_number, total, tax
    assert inv.invoice_number == "HD-TM-2026-77"
    assert inv.vendor == "HÓA ĐƠN THƯƠNG MẠI"  # regex vendor (label "Đơn vị bán" không khớp pattern)
    assert inv.total == 94050000.0
    assert inv.tax == 8550000.0  # regex tax + LLM tax giống nhau
    assert inv.confidence == 1.0


def test_production_fallback_skipped_when_regex_sufficient(monkeypatch):
    """(c) Regex đọc tốt → KHÔNG gọi LLM (không tốn token, giữ giá trị regex)."""
    _patch(monkeypatch, ExplodingProvider())

    inv = extract_invoice(SAMPLE_INVOICE.encode("utf-8"), "text/plain", "sample.txt")

    assert inv.invoice_number == "INV-2024-001"
    assert inv.vendor == "TechCorp Ltd"
    assert inv.total == 1000.0


def test_production_llm_failure_does_not_crash(monkeypatch):
    """(d) LLM trả rác / ném lỗi → extraction không crash, giữ kết quả regex."""
    _patch(monkeypatch, MockProvider("không phải json"))

    inv = extract_invoice(ODD_INVOICE.encode("utf-8"), "text/plain", "odd.txt")

    assert inv is not None
    assert inv.total == 0.0  # regex không đọc được, LLM hỏng → không merge
    assert inv.confidence == 0.6  # 3 fields / 5 = 0.6 (vendor, issue_date, tax, currency)
    # provider được gọi nhưng trả {} → không boost confidence


def test_production_llm_disabled_keeps_legacy_behavior(monkeypatch):
    """Chưa cấu hình key → get_llm_provider() None → hành vi cũ (chỉ regex),
    và kết quả bằng extract_from_text không có llm."""
    _patch(monkeypatch, None)

    inv = extract_invoice(ODD_INVOICE.encode("utf-8"), "text/plain", "odd.txt")
    ref = extract_from_text(ODD_INVOICE)

    assert inv.invoice_number == ref.invoice_number
    assert inv.total == ref.total == 0.0
    assert inv.confidence == ref.confidence


def test_production_valid_invoice_unchanged():
    """(e) Hóa đơn mẫu đọc đúng như trước — không retrofit LLM, provider off."""
    inv = extract_invoice(
        SAMPLE_INVOICE.encode("utf-8"), "text/plain", "sample.txt", user_id="u1"
    )
    assert inv.invoice_number == "INV-2024-001"
    assert inv.vendor == "TechCorp Ltd"
    assert inv.issue_date == "2024-06-15"
    assert inv.due_date == "2024-07-15"
    assert inv.total == 1000.0
    assert inv.user_id == "u1"
    assert inv.id


def test_production_sourcefile_and_id_set_for_worker():
    """Worker nối tiếp _qr_decode/gán user_id — id không được sinh từ invoice_number."""
    inv = extract_invoice(b"noop", "text/plain", "worker-key/file.txt")
    assert inv.source_file == "worker-key/file.txt"
    assert inv.id and inv.id != ext._make_id(inv.invoice_number, inv.source_file)


def test_worker_task_shares_production_entrypoint():
    """Worker/API/camera-upload đều gọi extractor.extract_invoice — sửa một điểm
    là đủ toàn bộ đường production."""
    import src.workers.extraction_worker as worker

    assert worker.extract_fn is ext.extract_invoice


def test_llm_call_threshold_still_gates_production(monkeypatch):
    """Ngưỡng LLM_CALL_THRESHOLD vẫn tác động qua đường production."""
    provider = CountingProvider()
    _patch(monkeypatch, provider)
    monkeypatch.setenv("LLM_CALL_THRESHOLD", "0.0")

    extract_invoice(SAMPLE_INVOICE.encode("utf-8"), "text/plain", "sample.txt")

    assert provider.calls == 0, "regex gần hoàn hảo + ngưỡng 0 phải skip LLM"


def test_llm_provider_resolved_from_llm_module(monkeypatch):
    """get_llm_provider default (không config key) None; có key → provider thật."""
    from src.llm.base import get_llm_provider

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_llm_provider() is None  # upload vẫn chạy khi thiếu key

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    p = get_llm_provider()
    assert p is not None and p.model and p.base_url


def test_openai_provider_reuses_client(monkeypatch):
    """Không tạo HTTP client mới mỗi request — client cache trong provider instance."""
    import sys
    import types
    import src.llm.base as base

    created = []

    class _Msg:
        content = '{"total": 1.0}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None):
            created.append(base_url)
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kw: _Resp()
                )
            )

    # Property `client` import OpenAI lúc gọi → thay module openai trong sys.modules
    fake = types.ModuleType("openai")
    fake.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setenv("LLM_API_KEY", "k")

    provider = base.get_llm_provider()
    assert provider.complete("s", "u") == '{"total": 1.0}'
    assert provider.complete("s", "u") == '{"total": 1.0}'
    assert len(created) == 1, "OpenAI client phải dùng lại, không tạo mỗi lần gọi"
    assert provider.client is provider.client
