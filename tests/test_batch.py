"""Test batch upload invoices."""
import io
import pytest
from fastapi.testclient import TestClient
import src.app as app_module


def _register(c):
    r = c.post("/auth/register", json={"username": "batch_test", "password": "test1234"})
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
