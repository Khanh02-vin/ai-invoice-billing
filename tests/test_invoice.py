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
