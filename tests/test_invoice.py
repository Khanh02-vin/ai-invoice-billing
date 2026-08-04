"""Tests cho Invoice & Billing System."""
import json
import tempfile
from pathlib import Path

from src.domain.models import Invoice, InvoiceStatus, InvoiceUpdate
from src.extract.extractor import extract_from_text, extract_invoice
from src.store.repository import InvoiceRepository


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


def test_extract_fields():
    """Trích xuất đủ các trường hóa đơn."""
    inv = extract_from_text(SAMPLE_INVOICE)
    assert inv.invoice_number == "INV-2024-001"
    assert inv.vendor.startswith("TechCorp")
    assert inv.issue_date == "2024-06-15"
    assert inv.due_date == "2024-07-15"
    assert inv.total == 1000.0
    assert inv.tax == 50.0
    assert inv.currency == "USD"


def test_extract_file():
    """Trích xuất từ file text."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(SAMPLE_INVOICE)
        path = f.name
    try:
        inv = extract_invoice(path)
        assert inv.invoice_number == "INV-2024-001"
        assert inv.source_file == path
    finally:
        Path(path).unlink()


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
    updated = repo.update(inv.id, InvoiceUpdate(status=InvoiceStatus.PAID), "u1")
    assert updated.status == InvoiceStatus.PAID
    assert repo.get(inv.id, "u1").status == InvoiceStatus.PAID


def test_repository_list_filter():
    """Lọc hóa đơn theo status."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    paid = repo.list(user_id="u1", status=InvoiceStatus.PAID)
    unpaid = repo.list(user_id="u1", status=InvoiceStatus.UNPAID)
    assert len(paid) == 0
    assert len(unpaid) == 1


def test_multi_user_isolation():
    """Hóa đơn của user này không thấy bởi user khác."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    # u2 không thấy hóa đơn của u1
    assert repo.get(inv.id, "u2") is None
    assert len(repo.list(user_id="u2")) == 0
    assert repo.update(inv.id, InvoiceUpdate(status=InvoiceStatus.PAID), "u2") is None
    assert repo.delete(inv.id, "u2") is False
    # u1 vẫn còn hóa đơn
    assert repo.get(inv.id, "u1") is not None


def test_monthly_report():
    """Báo cáo theo tháng."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    report = repo.monthly_report("2024-06", "u1")
    assert report is not None
    assert report.invoice_count == 1
    assert report.total_amount == 1000.0
    assert report.paid_count == 0
    assert report.unpaid_count == 1

    # Đánh dấu paid rồi kiểm tra lại
    repo.update(inv.id, InvoiceUpdate(status=InvoiceStatus.PAID), "u1")
    report = repo.monthly_report("2024-06", "u1")
    assert report.paid_count == 1
    assert report.paid_amount == 1000.0
    assert report.unpaid_count == 0


def test_monthly_report_empty_month():
    """Tháng không có hóa đơn trả về None."""
    repo = InvoiceRepository(db_path=":memory:")
    assert repo.monthly_report("2025-01", "u1") is None


def test_delete_invoice():
    """Xóa hóa đơn."""
    repo = InvoiceRepository(db_path=":memory:")
    inv = extract_from_text(SAMPLE_INVOICE)
    inv.user_id = "u1"
    repo.upsert(inv)
    assert repo.delete(inv.id, "u1") is True
    assert repo.delete(inv.id, "u1") is False
    assert repo.get(inv.id, "u1") is None