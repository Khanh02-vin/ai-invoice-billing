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
    """Đăng ký + trả token."""
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
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
    r = client.post("/auth/login", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 200
    assert "access_token" in r.json()

    r = client.get("/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["username"] == "kien"
    assert "password_hash" not in r.json()  # không lộ hash


def test_duplicate_register_rejected(client):
    """Username trùng → 400."""
    _register(client)
    r = client.post("/auth/register", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 400


def test_wrong_password_rejected(client):
    """Sai mật khẩu → 401."""
    _register(client)
    r = client.post("/auth/login", json={"username": "kien", "password": "sai-mat-khau"})
    assert r.status_code == 401


def test_invoices_require_auth(client):
    """Không token → 401."""
    assert client.get("/invoices").status_code == 401
    assert client.post("/invoices", json={}).status_code == 401
    assert client.patch("/invoices/x", json={}).status_code == 401


def test_invalid_token_rejected(client):
    """Token sai → 401."""
    r = client.get("/invoices", headers=_auth("token.rau.tom"))
    assert r.status_code == 401


# ---------- Luồng hóa đơn ----------

def test_upload_gtgt_full_flow(client):
    """Upload GTGT → trích xuất đúng → báo cáo tháng."""
    token = _register(client)
    r = client.post("/invoices/upload", headers=_auth(token),
                    files={"file": ("hd.txt", GTGT, "text/plain")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["invoice_number"] == "00012345"
    assert d["vendor"] == "CÔNG TY TNHH ABC"
    assert d["total"] == 30900000.0
    assert d["tax"] == 2900000.0
    assert d["discount"] == 1000000.0
    assert d["currency"] == "VND"
    assert d["issue_date"] == "2026-08-04"

    # Báo cáo tháng
    r = client.get("/reports/monthly/2026-08", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["invoice_count"] == 1
    assert r.json()["total_amount"] == 30900000.0


def test_create_list_paid_flow(client):
    """Tạo thủ công → list → đánh dấu paid."""
    token = _register(client)
    r = client.post("/invoices", headers=_auth(token),
                    json={"invoice_number": "INV-1", "vendor": "Vendor A", "total": 100.0})
    assert r.status_code == 200
    inv_id = r.json()["id"]

    r = client.get("/invoices", headers=_auth(token))
    assert len(r.json()) == 1

    r = client.patch(f"/invoices/{inv_id}", headers=_auth(token),
                     json={"status": "paid"})
    assert r.status_code == 200
    assert r.json()["status"] == "paid"

    # Lọc theo status
    r = client.get("/invoices?status=paid", headers=_auth(token))
    assert len(r.json()) == 1
    r = client.get("/invoices?status=unpaid", headers=_auth(token))
    assert len(r.json()) == 0


def test_multi_user_isolation_api(client):
    """User B không thấy hóa đơn của User A."""
    token_a = _register(client, "usera")
    token_b = _register(client, "userb")

    r = client.post("/invoices", headers=_auth(token_a),
                    json={"invoice_number": "SECRET-1", "vendor": "A", "total": 999.0})
    inv_id = r.json()["id"]

    # B xem hóa đơn A → 404
    assert client.get(f"/invoices/{inv_id}", headers=_auth(token_b)).status_code == 404
    # B sửa → 404
    r = client.patch(f"/invoices/{inv_id}", headers=_auth(token_b), json={"status": "paid"})
    assert r.status_code == 404
    # B list → rỗng
    assert client.get("/invoices", headers=_auth(token_b)).json() == []
    # A vẫn thấy
    assert len(client.get("/invoices", headers=_auth(token_a)).json()) == 1


def test_report_empty_month(client):
    """Báo cáo tháng không có hóa đơn → 404."""
    token = _register(client)
    r = client.get("/reports/monthly/2025-01", headers=_auth(token))
    assert r.status_code == 404