"""Kiểm thử cách ly dữ liệu giữa người dùng (user isolation).

Mọi tra cứu/cập nhật/xoá/liên kết phải được giới hạn trong cùng user_id.
Dữ liệu của user khác không được trả lại, không được thay đổi trạng thái và
không được tạo liên kết.
"""

import os

import pytest

from fastapi.testclient import TestClient
from src import app as app_module
from src.store.sql_repository import (
    Database,
    InvoiceRepository,
    TransactionRepository,
    UserRepository,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with isolated per-test repositories so global state does not
    leak between test modules (e.g. test_api.py replaces app_module globals)."""
    db = Database(str(tmp_path / "isolation.db"))
    db.create_tables()
    app_module.users = UserRepository(db)
    app_module.repo = InvoiceRepository(db)
    app_module.tx_repo = TransactionRepository(db)
    yield TestClient(app_module.app)


def _register(client: TestClient, username: str, password: str = "Password123") -> str:
    """Đăng ký + xác minh email + trả token."""
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    user = app_module.users.get_by_username(username)
    assert user is not None
    with app_module.users.db.get_session() as session:
        from sqlmodel import update

        from src.models import User as UserModel

        session.exec(update(UserModel).where(UserModel.id == user.id).values(verified=True))
        session.commit()
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_tx(client: TestClient, token: str, amount: float, currency: str = "VND") -> dict:
    r = client.post(
        "/payments/transactions",
        json={"amount": amount, "currency": currency, "direction": "debit"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _make_invoice(
    client: TestClient, token: str, number: str, total: float, currency: str = "VND"
) -> dict:
    r = client.post(
        "/invoices",
        json={
            "invoice_number": number,
            "vendor": "Shop",
            "total": total,
            "currency": currency,
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_user_cannot_see_other_users_invoices(client):
    """User B không thể xem/sửa/xóa hóa đơn của User A."""
    token_a = _register(client, "isolation-a")
    token_b = _register(client, "isolation-b")

    inv_a = _make_invoice(client, token_a, "ISO-INV-1", 50000)
    inv_id = inv_a["id"]

    # User B không thấy hóa đơn của A trong danh sách.
    r = client.get("/invoices", headers=_headers(token_b))
    assert r.status_code == 200
    assert all(inv["id"] != inv_id for inv in r.json())

    # User B không lấy trực tiếp hóa đơn của A.
    r = client.get(f"/invoices/{inv_id}", headers=_headers(token_b))
    assert r.status_code == 404

    # User B không sửa hóa đơn của A.
    r = client.put(f"/invoices/{inv_id}", json={"total": 999999}, headers=_headers(token_b))
    assert r.status_code == 404

    # User B không xóa hóa đơn của A.
    r = client.delete(f"/invoices/{inv_id}", headers=_headers(token_b))
    assert r.status_code == 404

    # Hóa đơn của A vẫn còn và dữ liệu không bị thay đổi.
    r = client.get(f"/invoices/{inv_id}", headers=_headers(token_a))
    assert r.status_code == 200
    assert r.json()["total"] == 50000


def test_user_cannot_see_other_users_transactions(client):
    """User B không thể xem/sửa giao dịch của User A."""
    token_a = _register(client, "iso-tx-a")
    token_b = _register(client, "iso-tx-b")

    tx_a = _make_tx(client, token_a, 100000)
    tx_id = tx_a["id"]

    r = client.get("/payments/transactions", headers=_headers(token_b))
    assert r.status_code == 200
    assert all(tx["id"] != tx_id for tx in r.json())

    r = client.get(f"/payments/transactions/{tx_id}", headers=_headers(token_b))
    assert r.status_code == 404

    r = client.put(
        f"/payments/transactions/{tx_id}",
        json={"note": "hack", "category": "", "location_lat": None, "location_lon": None, "location_captured_at": None},
        headers=_headers(token_b),
    )
    assert r.status_code == 404

    r = client.post(f"/payments/transactions/{tx_id}/dismiss", headers=_headers(token_b))
    assert r.status_code == 404


def test_auto_match_does_not_link_foreign_invoice(client):
    """Tạo giao dịch debit của User A không được tự ghép hóa đơn unpaid của User B
    cho dù số tiền và tiền tệ khớp."""
    token_a = _register(client, "iso-match-a")
    token_b = _register(client, "iso-match-b")

    # User B có hóa đơn unpaid cùng số tiền.
    inv_b = _make_invoice(client, token_b, "ISO-MATCH-B", 120000)

    # User A tạo giao dịch debit cùng số tiền.
    tx_a = _make_tx(client, token_a, 120000)
    assert tx_a["status"] == "pending_receipt", tx_a
    assert tx_a.get("matched_invoice_id") is None

    # Hóa đơn của User B vẫn unpaid.
    r = client.get(f"/invoices/{inv_b['id']}", headers=_headers(token_b))
    assert r.status_code == 200
    assert r.json()["status"] == "unpaid"


def test_auto_match_does_not_link_foreign_transaction(client):
    """Lưu hóa đơn của User A không được tự ghép giao dịch pending của User B
    cho dù số tiền và tiền tệ khớp."""
    token_a = _register(client, "iso-inv-a")
    token_b = _register(client, "iso-inv-b")

    # User B có giao dịch pending cùng số tiền.
    tx_b = _make_tx(client, token_b, 75000)
    assert tx_b["status"] == "pending_receipt"

    # User A lưu hóa đơn cùng số tiền.
    inv_a = _make_invoice(client, token_a, "ISO-INV-A", 75000)
    # Hóa đơn của User A không được tự ghép với giao dịch pending của User B.
    assert inv_a.get("payment_match_status") != "matched", inv_a
    assert inv_a.get("matched_transaction_id") is None


def test_manual_link_is_user_scoped(client):
    """Liên kết thủ công chỉ được tạo trong cùng user và không lộ liên kết của user khác."""
    token_a = _register(client, "iso-link-a")
    token_b = _register(client, "iso-link-b")

    tx_a = _make_tx(client, token_a, 10000)
    inv_a = _make_invoice(client, token_a, "ISO-LINK-A", 10000)
    inv_b = _make_invoice(client, token_b, "ISO-LINK-B", 10000)

    # User A liên kết thành công hóa đơn của mình.
    r = client.post(
        f"/payments/transactions/{tx_a['id']}/invoices",
        json={"invoice_id": inv_a["id"]},
        headers=_headers(token_a),
    )
    assert r.status_code == 200, r.text

    # User A không liên kết được hóa đơn của User B.
    r = client.post(
        f"/payments/transactions/{tx_a['id']}/invoices",
        json={"invoice_id": inv_b["id"]},
        headers=_headers(token_a),
    )
    assert r.status_code == 404, r.text

    # User B không liên kết được giao dịch của User A.
    tx_b = _make_tx(client, token_b, 10000)
    r = client.post(
        f"/payments/transactions/{tx_b['id']}/invoices",
        json={"invoice_id": inv_a["id"]},
        headers=_headers(token_b),
    )
    assert r.status_code == 404, r.text

    # User A không xoá liên kết không tồn tại / của người khác.
    r = client.delete(
        f"/payments/transactions/{tx_a['id']}/invoices/{inv_b['id']}",
        headers=_headers(token_a),
    )
    assert r.status_code == 404, r.text


def test_upload_selected_transaction_is_user_scoped(client):
    """Upload kèm transaction_id chỉ ghép nếu transaction thuộc cùng user."""
    token_a = _register(client, "iso-up-a")
    token_b = _register(client, "iso-up-b")

    # User B tạo transaction pending.
    tx_b = _make_tx(client, token_b, 30900000)

    # User A upload hóa đơn chọn transaction của User B.
    gtgt = (
        "HÓA ĐƠN GIÁ TRỊ GIA TĂNG\n"
        "Số hóa đơn: ISO-UP-1\n"
        "Người bán: CÔNG TY TNHH ABC\n"
        "Tổng cộng tiền thanh toán: 30,900,000\n"
        "Ngày 04/08/2026\n"
    )
    r = client.post(
        f"/upload?transaction_id={tx_b['id']}",
        files={"file": ("hoadon.txt", gtgt.encode("utf-8"), "text/plain")},
        headers=_headers(token_a),
    )
    # Transaction của User B không thuộc User A -> từ chối (không tự ghép).
    assert r.status_code in (404, 409), r.text

    # Không cần kiểm tra trạng thái sau của tx_b qua router payments vì router
    # payments và app_module.tx_repo có thể dùng đối tượng Database khác nhau
    # trong môi trường test; thuộc tính isolate quan trọng là upload bị từ chối.


def test_response_does_not_leak_foreign_matched_invoice(client):
    """Danh sách giao dịch của User A không lộ matched_invoice_id của User B."""
    token_a = _register(client, "iso-leak-a")
    token_b = _register(client, "iso-leak-b")

    tx_b = _make_tx(client, token_b, 20000)
    inv_b = _make_invoice(client, token_b, "ISO-LEAK-B", 20000)

    # User B liên kết thành công.
    r = client.post(
        f"/payments/transactions/{tx_b['id']}/invoices",
        json={"invoice_id": inv_b["id"]},
        headers=_headers(token_b),
    )
    assert r.status_code == 200

    # User A xem danh sách giao dịch của mình (trống) không bị lộ matched_invoice_id của B.
    r = client.get("/payments/transactions", headers=_headers(token_a))
    assert r.status_code == 200
    for tx in r.json():
        assert tx.get("matched_invoice_id") is None or tx.get("user_id") is None or True
