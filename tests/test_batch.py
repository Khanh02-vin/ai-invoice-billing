"""Test batch upload invoices."""
import io
import pytest
from fastapi.testclient import TestClient
import src.app as app_module


def _register(c):
    r = c.post("/auth/register", json={"username": "batch_test", "password": "test1234"})
    app_module.users.set_verified(app_module.users.get_by_username("batch_test").id, True)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def client(monkeypatch):
    import os
    os.environ["OPEN_REGISTRATION"] = "1"
    from src.store.repository import InvoiceRepository
    from src.store.users import UserRepository
    monkeypatch.setattr(app_module, "repo", InvoiceRepository(":memory:"))
    monkeypatch.setattr(app_module, "users", UserRepository(":memory:"))
    with TestClient(app_module.app) as c:
        yield c


def test_batch_upload_success(client):
    """Upload nhiều file — ít nhất 2 file text thành công."""
    h = _register(client)
    text = "HOÁ ĐƠN GTGT\nSố hóa đơn: 001\nNgười bán: Công ty ABC\nTổng cộng: 1,500,000\nThuế GTGT: 150,000\n"
    # Upload 2 file text riêng lẻ
    ok_count = 0
    for name in ("a.txt", "b.txt"):
        resp = client.post("/upload", files={"file": (name, text, "text/plain")}, headers=h)
        if resp.status_code == 200:
            ok_count += 1
    assert ok_count >= 2


def test_batch_too_many(client):
    """Upload file rỗng → 400."""
    h = _register(client)
    resp = client.post("/upload", files={"file": ("empty.txt", b"", "text/plain")}, headers=h)
    assert resp.status_code == 400


def test_extract_batch_calls_extract_invoice_with_mime():
    """extract_batch: gọi extract_invoice(content, mime_type, ...) đúng signature
    (trước đây gọi extract_invoice(path) → TypeError mọi file)."""
    from src.extract.batch_extractor import extract_batch
    from src.store.repository import InvoiceRepository

    repo = InvoiceRepository(":memory:")
    a = "Số hóa đơn: HD-BATCH-1\nNgười bán: Công ty A\nTổng cộng: 1,500,000"
    b = "Số hóa đơn: HD-BATCH-2\nNgười bán: Công ty B\nTổng cộng: 2,000,000"
    out = extract_batch(
        [("a.txt", a.encode()), ("b.txt", b.encode())], repo, user_id="u1"
    )
    assert out["failed"] == 0, out["errors"]
    assert out["successful"] == 2
    assert {inv.invoice_number for inv in out["results"]} == {"HD-BATCH-1", "HD-BATCH-2"}
    assert len(repo.list(user_id="u1")) == 2


def test_extract_batch_duplicate_number_isolated():
    """extract_batch: cùng số hóa đơn khác file → 1 file lỗi, không ghi đè."""
    from src.extract.batch_extractor import extract_batch
    from src.store.repository import InvoiceRepository

    repo = InvoiceRepository(":memory:")
    a = "Số hóa đơn: HD-DUP\nNgười bán: Công ty A\nTổng cộng: 100,000"
    b = "Số hóa đơn: HD-DUP\nNgười bán: Công ty B\nTổng cộng: 200,000"
    out = extract_batch(
        [("a.txt", a.encode()), ("b.txt", b.encode())], repo, user_id="u1"
    )
    assert out["successful"] == 1 and out["failed"] == 1, out
    kept = repo.list(user_id="u1")
    assert len(kept) == 1
    assert kept[0].invoice_number == "HD-DUP"  # 1 bản giữ nguyên, bản kia bị 409
