"""API integration tests — luồng thật: register → login → upload → report.
Dùng :memory: cho repo/users, không đụng DB file."""
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from src import app as app_module
from src.store.repository import InvoiceRepository
from src.store.users import UserRepository


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Mỗi test dùng DB :memory: mới. Đăng ký mở cho test."""
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    app_module.repo = InvoiceRepository(db_path=":memory:")
    app_module.users = UserRepository(db_path=":memory:")
    yield
    # dọn invoices.db nếu import app tạo ra
    Path("invoices.db").unlink(missing_ok=True)


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username="kien", password="matkhau123") -> str:
    """Đăng ký + xác minh email + trả token."""
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


GTGT = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00012345
Người bán: CÔNG TY TNHH ABC
Cộng tiền hàng hóa, dịch vụ: 29,000,000
Chiết khấu thương mại: 1,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 30,900,000
Ngày 04/08/2026
"""


# ---------- Auth ----------

def test_register_login_flow(client):
    """Đăng ký → đăng nhập → me."""
    token = _register(client)
    app_module.users.set_verified(app_module.users.get_by_username("kien").id, True)
    r = client.post("/auth/login", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 200
    assert "access_token" in r.json()

    r = client.get("/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["username"] == "kien"
    assert "password_hash" not in r.json()  # không lộ hash


def test_duplicate_register_rejected(client):
    """Username trùng → 409."""
    _register(client)
    r = client.post("/auth/register", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 409


def test_wrong_password_rejected(client):
    """Sai mật khẩu → 401."""
    _register(client)
    r = client.post("/auth/login", json={"username": "kien", "password": "wrongpassword"})
    assert r.status_code == 401


def test_invoices_require_auth(client):
    """API hóa đơn yêu cầu auth → 401."""
    r = client.get("/invoices")
    assert r.status_code == 401


def test_invalid_token_rejected(client):
    """Token sai → 401."""
    r = client.get("/invoices", headers=_auth("token-sai"))
    assert r.status_code == 401


# ---------- Upload & Extraction ----------

def test_upload_gtgt_full_flow(client):
    """Upload GTGT → trích xuất → lấy invoice."""
    token = _register(client)
    # Tạo file text giả lập
    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["invoice_number"] == "00012345"
    assert inv["vendor"] == "CÔNG TY TNHH ABC"
    assert inv["total"] == 30900000 or inv["total"] == 30900000.0


# ---------- Invoice CRUD ----------

def test_create_list_paid_flow(client):
    """Tạo hóa đơn → list → cập nhật paid."""
    token = _register(client)
    # Tạo hóa đơn
    r = client.post(
        "/invoices",
        json={"invoice_number": "INV-001", "vendor": "Test", "total": 100.0},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]

    # List
    r = client.get("/invoices", headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Update to paid
    r = client.put(
        f"/invoices/{inv_id}",
        json={"status": "paid"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "paid"


def test_multi_user_isolation_api(client):
    """User 2 không thấy invoice của user 1."""
    token1 = _register(client, "user1", "pass123456")
    token2 = _register(client, "user2", "pass123456")

    # User 1 tạo invoice
    r = client.post(
        "/invoices",
        json={"invoice_number": "INV-001", "vendor": "Test", "total": 100.0},
        headers=_auth(token1),
    )
    assert r.status_code == 200
    inv_id = r.json()["id"]

    # User 2 không thấy
    r = client.get(f"/invoices/{inv_id}", headers=_auth(token2))
    assert r.status_code == 404


def test_report_empty_month(client):
    """Báo cáo tháng trống → 404."""
    token = _register(client)
    r = client.get("/reports/monthly/2024-01", headers=_auth(token))
    assert r.status_code == 404
