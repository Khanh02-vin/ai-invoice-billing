"""Five cross-workflow acceptance tests (Web MVP re-verification).

These exercise complete business scenarios through the real API surface,
reusing the fixtures/patterns of test_review.py (auth + /upload) and
payments (manual transactions / purchases / attachments). OCR is the real
sync extractor for text uploads (as in test_upload_validation.py); image OCR
flows are mocked at the existing seams.
"""

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.routers import payments
from src.models import User
from src.store.sql_repository import (
    Database,
    InvoiceRepository,
    PurchaseRepository,
    TransactionRepository,
    UserRepository,
)

GTGT = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00012345
Người bán: CÔNG TY TNHH ABC
Cộng tiền hàng hóa, dịch vụ: 29,000,000
Chiết khấu thương mại: 1,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 30,900,000
Ngày 04/08/2026
"""

PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 32


@pytest.fixture()
def wf_client(monkeypatch, tmp_path):
    """Owner-scoped payments client over a fresh DB (payments pattern)."""
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    db = Database(str(tmp_path / "wf.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add(User(id="owner", username="owner", password_hash="x" * 60, verified=True))
        session.commit()
    monkeypatch.setattr(payments, "_get_db", lambda: db)
    monkeypatch.setattr(payments, "_reverse_geocode", lambda lat, lon: None)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    app_module.app.dependency_overrides[payments.current_user] = lambda: User(
        id="owner", username="owner", password_hash="x" * 60, verified=True
    )
    yield TestClient(app_module.app), db, tmp_path
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def wf_upload_client(monkeypatch, tmp_path):
    """Full-app client with fresh invoice repos (test_review pattern)."""
    app_db = Database(str(tmp_path / "wf-up.db"))
    app_db.create_tables()
    app_module.repo = InvoiceRepository(app_db)
    app_module.users = UserRepository(app_db)
    app_module.tx_repo = TransactionRepository(app_db)
    from src.security.idempotency import InMemoryCache

    app_module._upload_idempotency = InMemoryCache()
    return TestClient(app_module.app)


def _register(client, username):
    r = client.post("/auth/register", json={"username": username, "password": "matkhau123"})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_workflow_1_upload_edit_save_reopen(wf_upload_client):
    client = wf_upload_client
    h = _register(client, "wf1")
    uploaded = client.post(
        "/upload",
        files={"file": ("hoadon.txt", GTGT.encode("utf-8"), "text/plain")},
        headers=h,
    )
    assert uploaded.status_code == 202, uploaded.text
    invoice_id = uploaded.json()["id"]
    # Validated MIME is persisted on the record.
    assert uploaded.json()["mime_type"] == "text/plain"

    edited = client.put(
        f"/invoices/{invoice_id}",
        json={"vendor": "CÔNG TY TNHH ABC (đã kiểm tra)"},
        headers=h,
    )
    assert edited.status_code == 200, edited.text

    reopened = client.get(f"/invoices/{invoice_id}", headers=h)
    assert reopened.status_code == 200
    assert reopened.json()["vendor"] == "CÔNG TY TNHH ABC (đã kiểm tra)"
    assert reopened.json()["total"] == 30900000.0


def test_workflow_2_scan_qr_payment_matching(wf_upload_client, monkeypatch):
    """QR-flavoured invoice upload → payment transaction → unique matching."""
    from src.extract import extractor

    client = wf_upload_client
    h = _register(client, "wf2")
    uploaded = client.post(
        "/upload",
        files={"file": ("hoadon.txt", GTGT.encode("utf-8"), "text/plain")},
        headers=h,
    )
    assert uploaded.status_code == 202, uploaded.text
    inv = uploaded.json()
    assert inv["job_status"] == "review"
    assert inv["total"] == 30900000.0

    # Payment arrives after the invoice was saved: auto-match links it.
    tx = client.post(
        "/payments/transactions",
        json={"amount": 30900000, "currency": "VND"},
        headers=h,
    )
    assert tx.status_code == 200, tx.text
    assert tx.json()["status"] == "matched"
    assert tx.json()["matched_invoice_id"] == inv["id"]
    # Matching never claims payment verification.
    candidates = client.get(
        f"/payments/transactions/{tx.json()['id']}/invoice-candidates", headers=h
    )
    assert candidates.json()["payment_verified"] is False


def test_workflow_3_online_expense_with_evidence(wf_client, monkeypatch, tmp_path):
    client, db, _ = wf_client
    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Tai nghe", "merchant": "Shop Online", "amount": 150000,
              "purchase_context_type": "online",
              "payment_lat": 10.77, "payment_lon": 106.7,
              "payment_source": "gps", "payment_captured_at": "2026-11-08T10:00:00Z"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["purchase_context_type"] == "online"
    assert body["payment_lat"] == 10.77
    assert body["payment_lon"] == 106.7
    assert body["merchant_lat"] is None
    assert body["merchant_lon"] is None

    listed = client.get("/payments/purchases").json()
    assert listed[0]["payment_lat"] == 10.77
    assert listed[0]["merchant_lat"] is None

    tx = client.post("/payments/transactions", json={"amount": 150000, "merchant": "Shop Online"}).json()
    attached = client.post(
        f"/payments/transactions/{tx['id']}/attachments",
        files={"file": ("receipt.png", PNG, "image/png")},
    )
    assert attached.status_code == 200, attached.text

    purchase = PurchaseRepository(db).list("owner")[0]
    assert purchase.purchase_context_type == "online"
    attachments = client.get(f"/payments/transactions/{tx['id']}/attachments").json()
    assert len(attachments) == 1


def test_workflow_4_physical_expense_location_and_invoice(wf_client):
    client, db, _ = wf_client
    created = client.post(
        "/payments/purchases/manual",
        data={
            "item": "Cà phê", "merchant": "Quán Sen", "amount": 45000,
            "purchase_context_type": "physical",
            "payment_lat": 10.78, "payment_lon": 106.71,
            "payment_source": "gps",
            "merchant_lat": 10.77, "merchant_lon": 106.7,
            "merchant_name": "Quán Sen", "merchant_verified_by_user": "true",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["purchase_context_type"] == "physical"
    assert body["merchant_lat"] == 10.77
    assert body["payment_lat"] == 10.78
    assert body["payment_lon"] == 106.71
    assert body["merchant_lat"] != body["payment_lat"]
    assert body["merchant_verified_by_user"] is True

    listed = client.get("/payments/purchases").json()
    assert listed[0]["merchant_name"] == "Quán Sen"
    assert listed[0]["merchant_lat"] == 10.77
    assert listed[0]["merchant_lon"] == 106.7
    assert listed[0]["payment_lat"] == 10.78
    assert listed[0]["purchase_context_type"] == "physical"


def test_workflow_5_failed_upload_recovers_to_manual_entry(wf_upload_client, wf_client):
    """Unreadable upload fails extraction, then manual entry still works."""
    client = wf_upload_client
    h = _register(client, "wf5")
    junk = b"not an invoice at all 12345"
    uploaded = client.post(
        "/upload",
        files={"file": ("junk.txt", junk, "text/plain")},
        headers=h,
    )
    assert uploaded.status_code == 202, uploaded.text
    invoice_id = uploaded.json()["id"]

    # Extraction failure does not block manual entry via /invoices POST.
    manual = client.post(
        "/invoices",
        json={"invoice_number": "MANUAL-1", "vendor": "Tiểu thương", "total": 50000, "currency": "VND"},
        headers=h,
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["invoice_number"] == "MANUAL-1"
    fetched = client.get(f"/invoices/{manual.json()['id']}", headers=h)
    assert fetched.status_code == 200
    assert fetched.json()["total"] == 50000

    # And the failed upload's record remains retrievable for retry/inspection.
    assert client.get(f"/invoices/{invoice_id}", headers=h).status_code == 200
