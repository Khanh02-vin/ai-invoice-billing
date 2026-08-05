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
    h = _register(client)
    text = "HOÁ ĐƠN GTGT\nSố hóa đơn: 001\nNgười bán: Công ty ABC\nTổng cộng: 1,500,000\nThuế GTGT: 150,000\n"
    files = [
        ("a.txt", text, "text/plain"),
        ("b.txt", text, "text/plain"),
        ("c.pdf", b"%PDF-1.4 malformed data", "application/pdf"),
    ]
    resp = client.post("/invoices/upload-bulk", files=[("files", f) for f in files], headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["successful"] >= 2
    # file pdf lỗi bị skip, không 500

def test_batch_too_many(client):
    h = _register(client)
    files = [("f.txt", b"x", "text/plain") for _ in range(25)]
    resp = client.post("/invoices/upload-bulk", files=[("files", f) for f in files], headers=h)
    assert resp.status_code == 400