"""Chống trùng invoice — cùng file gộp, cùng số hóa đơn khác file → 409.

Repository: unique index (user, file_checksum) + (user, invoice_number) và check
trước mỗi insert. 'unknown'/'', '' là số chưa trích được (mọi bill đều có) → không chặn.
"""

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.domain.models import Invoice
from src.store.repository import InvoiceRepository, DuplicateInvoiceError
from src.store.users import UserRepository


def _inv(id: str, user_id: str = "u1", number: str = "INV-1", checksum: str = "") -> Invoice:
    return Invoice(id=id, user_id=user_id, invoice_number=number, file_checksum=checksum, total=1.0)


def test_same_file_merges_into_existing():
    """Upload lại cùng file (kể cả OCR lệch số) → gộp vào bản cũ, không tạo bản 2."""
    repo = InvoiceRepository(db_path=":memory:")
    first = repo.upsert(_inv("a", checksum="fp1", number="INV-1"))
    merged = repo.upsert(_inv("b", checksum="fp1", number="INV-1-LECH"))
    assert merged.id == first.id
    assert len(repo.list(user_id="u1")) == 1


def test_same_number_other_file_rejected():
    """Cùng số hóa đơn nhưng file khác → lỗi, không ghi đè âm thầm."""
    repo = InvoiceRepository(db_path=":memory:")
    repo.upsert(_inv("a", checksum="fp1", number="INV-9"))
    with pytest.raises(DuplicateInvoiceError) as e:
        repo.upsert(_inv("b", checksum="fp2", number="INV-9"))
    assert e.value.existing_id == "a"
    assert len(repo.list(user_id="u1")) == 1


def test_unknown_number_not_deduped():
    """Số chưa trích được ('unknown') → hai bill khác nhau vẫn tạo 2 invoice."""
    repo = InvoiceRepository(db_path=":memory:")
    repo.upsert(_inv("a", number="unknown", checksum="fp1"))
    repo.upsert(_inv("b", number="unknown", checksum="fp2"))
    assert len(repo.list(user_id="u1")) == 2


def test_same_number_isolated_per_user():
    """User khác nhau cùng số hóa đơn → không ảnh hưởng nhau."""
    repo = InvoiceRepository(db_path=":memory:")
    repo.upsert(_inv("a", user_id="u1", number="INV-1"))
    repo.upsert(_inv("b", user_id="u2", number="INV-1"))
    assert len(repo.list(user_id="u1")) == 1
    assert len(repo.list(user_id="u2")) == 1


def test_race_two_uploads_fallback_merges(monkeypatch):
    """Hai request cùng lúc lọt qua check → IntegrityError → gộp lần 2."""
    repo = InvoiceRepository(db_path=":memory:")
    real = repo._find_duplicate
    calls = {"n": 0}

    def racy(conn, invoice):
        calls["n"] += 1
        if calls["n"] <= 2:  # cả2 pre-check đều "chưa thấy" (đọc trước khi kia ghi)
            return None
        return real(conn, invoice)

    monkeypatch.setattr(repo, "_find_duplicate", racy)
    a = repo.upsert(_inv("a", checksum="fp9"))
    b = repo.upsert(_inv("b", checksum="fp9"))
    assert b.id == a.id
    assert len(repo.list(user_id="u1")) == 1


# ---------- Qua API ----------

BILL = """HÓA ĐƠN
Số hóa đơn: HD-777
Người bán: Công ty ABC
Tổng cộng tiền thanh toán: 1,500,000
"""


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    monkeypatch.setattr(app_module, "repo", InvoiceRepository(":memory:"))
    monkeypatch.setattr(app_module, "users", UserRepository(":memory:"))
    with TestClient(app_module.app) as c:
        yield c


def _register(c, username: str) -> dict:
    r = c.post("/auth/register", json={"username": username, "password": "test1234"})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_upload_same_file_twice_one_invoice(client):
    """Cùng nội dung, tên file khác nhau → vẫn1 hóa đơn, id giữ nguyên."""
    h = _register(client, "dedup_user")
    data = BILL.encode("utf-8")
    r1 = client.post("/upload", files={"file": ("bill.txt", data, "text/plain")}, headers=h)
    r2 = client.post("/upload", files={"file": ("bill-copy.txt", data, "text/plain")}, headers=h)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]
    assert len(client.get("/invoices", headers=h).json()) == 1


def test_create_duplicate_number_409(client):
    """POST /invoices cùng số → 409, bản cũ giữ nguyên."""
    h = _register(client, "dedup_user2")
    body = {"invoice_number": "INV-777", "vendor": "A", "total": 10.0}
    assert client.post("/invoices", json=body, headers=h).status_code == 200
    r = client.post("/invoices", json=body, headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DUPLICATE_INVOICE"
    assert len(client.get("/invoices", headers=h).json()) == 1
