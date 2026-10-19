import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.models import Invoice, User
from src.routers import payments
from src.store.sql_repository import Database, TransactionRepository


@pytest.fixture
def payments_client(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "payments.db"))
    db.create_tables()
    with db.get_session() as session:
        user = User(id="owner", username="owner", password_hash="x" * 60, verified=True)
        other = User(id="other", username="other", password_hash="y" * 60, verified=True)
        session.add(user)
        session.add(other)
        session.commit()
    monkeypatch.setattr(payments, "_get_db", lambda: db)
    app_module.app.dependency_overrides[payments.current_user] = lambda: User(
        id="owner", username="owner", password_hash="x" * 60, verified=True
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def test_manual_transaction_create_matches_existing_unpaid_invoice(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository, InvoiceTransactionLinkRepository

    invoice = Invoice(id="pending-invoice", user_id="owner", invoice_number="I-100", total=1200, currency="VND")
    InvoiceRepository(db).upsert(invoice)

    created = client.post("/payments/transactions", json={"amount": 1200, "direction": "debit"})
    assert created.status_code == 200
    tx = created.json()
    assert tx["status"] == "matched"
    assert tx["matched_invoice_id"] == invoice.id
    assert InvoiceRepository(db).get(invoice.id, user_id="owner").status.value == "paid"
    assert len(InvoiceTransactionLinkRepository(db).list_for_transaction(tx["id"], "owner")) == 1


def test_manual_transaction_create_only_matches_eligible_amount_currency_and_direction(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    invoices = InvoiceRepository(db)
    invoices.upsert(Invoice(id="usd-invoice", user_id="owner", invoice_number="USD-1", total=1200, currency="USD"))
    invoices.upsert(Invoice(id="vnd-invoice", user_id="owner", invoice_number="VND-1", total=5000, currency="VND"))

    wrong_currency = client.post("/payments/transactions", json={"amount": 1200, "currency": "VND"}).json()
    credit = client.post("/payments/transactions", json={"amount": 5000, "direction": "credit"}).json()
    assert wrong_currency["status"] == "pending_receipt"
    assert wrong_currency["matched_invoice_id"] is None
    assert credit["status"] == "pending_receipt"
    assert credit["matched_invoice_id"] is None
    assert invoices.get("vnd-invoice", user_id="owner").status.value == "unpaid"


def test_transaction_crud_is_owner_scoped(payments_client):
    client, db = payments_client
    created = client.post("/payments/transactions", json={"amount": 1200, "merchant": "Cafe"})
    assert created.status_code == 200
    tx_id = created.json()["id"]
    assert client.get("/payments/transactions").json()[0]["merchant"] == "Cafe"

    TransactionRepository(db).create(__import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        user_id="other", amount=50, id="foreign"
    ))
    assert client.put(f"/payments/transactions/foreign", json={"note": "x", "category": "", "location_lat": None, "location_lon": None, "location_captured_at": None}).status_code == 404
    assert client.post("/payments/transactions/foreign/dismiss").status_code == 404
    assert client.post(f"/payments/transactions/{tx_id}/dismiss").json() == {"dismissed": True}


def test_manual_invoice_link_and_unlink_are_owner_scoped(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository, InvoiceTransactionLinkRepository

    tx = client.post("/payments/transactions", json={"amount": 100}).json()
    owner_invoice = Invoice(id="owner-invoice", user_id="owner", invoice_number="I-1", total=100)
    foreign_invoice = Invoice(id="foreign-invoice", user_id="other", invoice_number="I-2", total=100)
    tx_repo = InvoiceRepository(db)
    tx_repo.upsert(owner_invoice)
    tx_repo.upsert(foreign_invoice)

    linked = client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": owner_invoice.id},
    )
    assert linked.status_code == 200
    assert linked.json()["amount"] == 100
    assert len(InvoiceTransactionLinkRepository(db).list_for_transaction(tx["id"], "owner")) == 1

    assert client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": foreign_invoice.id},
    ).status_code == 404
    assert client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": owner_invoice.id, "amount": 101},
    ).status_code == 400
    assert client.delete(
        f"/payments/transactions/{tx['id']}/invoices/{owner_invoice.id}"
    ).json() == {"unlinked": True}
    assert not InvoiceTransactionLinkRepository(db).list_for_transaction(tx["id"], "owner")


    client, _ = payments_client
    assert client.post("/payments/transactions", json={"amount": -1}).status_code == 422
    assert client.post("/payments/transactions", json={"amount": 5, "location_lat": 10}).status_code == 422


def test_sharing_requires_opt_in_and_opt_out_deletes_visits(payments_client):
    client, db = payments_client
    place = {"name": "Cafe", "kind": "cafe", "lat": 10, "lon": 106}
    assert client.post("/payments/places/share", json=place).status_code == 403
    assert client.put("/payments/preferences/place-sharing?enabled=true").json()["share_place_visits"] is True
    assert client.post("/payments/places/share", json=place).json() == {"shared": True}
    assert client.put("/payments/preferences/place-sharing?enabled=false").json()["share_place_visits"] is False
    with db.get_session() as session:
        from sqlmodel import select
        from src.models import SharedPlaceVisit
        assert not session.exec(select(SharedPlaceVisit)).all()


def test_attachment_upload_download_is_owner_scoped(payments_client, monkeypatch, tmp_path):
    client, _ = payments_client
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    tx = client.post("/payments/transactions", json={"amount": 30}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    response = client.post(
        f"/payments/transactions/{tx['id']}/attachments",
        files={"file": ("receipt.png", png, "image/png")},
    )
    assert response.status_code == 200
    attachment_id = response.json()["id"]
    assert client.get(f"/payments/attachments/{attachment_id}/content").content == png


def test_partial_transaction_update_preserves_amount_and_location(payments_client):
    client, _ = payments_client
    tx = client.post("/payments/transactions", json={"amount": 875, "location_lat": 10, "location_lon": 106}).json()
    updated = client.put(f"/payments/transactions/{tx['id']}", json={"note": "Mua đồ"})
    assert updated.status_code == 200
    assert updated.json()["amount"] == 875
    assert updated.json()["location_lat"] == 10
    assert updated.json()["location_lon"] == 106


def test_attachment_to_pending_transaction_preserves_transfer_amount(payments_client, monkeypatch, tmp_path):
    client, _ = payments_client
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    tx = client.post("/payments/transactions", json={"amount": 2345, "merchant": "Ngân hàng"}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    attached = client.post(
        f"/payments/transactions/{tx['id']}/attachments",
        files={"file": ("product.png", png, "image/png")},
    )
    assert attached.status_code == 200
    unchanged = client.get("/payments/transactions").json()[0]
    assert unchanged["amount"] == 2345
    assert unchanged["id"] == tx["id"]
    assert len(client.get(f"/payments/transactions/{tx['id']}/attachments").json()) == 1


def test_attachment_rejects_foreign_transaction(payments_client, monkeypatch, tmp_path):
    client, db = payments_client
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    foreign = __import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        user_id="other", id="foreign-attachment", amount=25,
    )
    TransactionRepository(db).create(foreign)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    response = client.post(
        "/payments/transactions/foreign-attachment/attachments",
        files={"file": ("product.png", png, "image/png")},
    )
    assert response.status_code == 404


def test_osm_upstream_failure_returns_empty_suggestions(payments_client, monkeypatch):
    client, _ = payments_client
    def fail(*args, **kwargs):
        import httpx
        raise httpx.TimeoutException("timeout")
    monkeypatch.setattr(payments.httpx, "get", fail)
    response = client.get("/payments/places?lat=10&lon=106")
    assert response.status_code == 200
    assert response.json() == {"places": []}


def test_image_draft_falls_back_without_vision(payments_client, tmp_path, monkeypatch):
    client, _ = payments_client
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(payments, "get_llm_provider", lambda: None)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    response = client.post("/payments/image-draft", files={"file": ("item.png", png, "image/png")})
    assert response.status_code == 200
    assert response.json()["status"] == "manual_review_required"
    assert response.json()["amount"] is None
    assert client.get("/payments/transactions").json() == []


def test_image_draft_uses_vision_and_never_returns_amount(payments_client, monkeypatch):
    client, _ = payments_client

    class VisionProvider:
        def complete_image(self, system, prompt, image):
            assert "Không suy đoán giá" in system
            assert image.startswith("data:image/png;base64,")
            return '{"merchant":"Quán Sen","item":"Phở","category":"Ăn uống","address":"12 Đường A","packaging_text":"Bánh đa riêu cua"}'

    monkeypatch.setattr(payments, "get_llm_provider", lambda: VisionProvider())
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    response = client.post("/payments/image-draft", files={"file": ("item.png", png, "image/png")})
    assert response.status_code == 200
    assert response.json() == {
        "status": "review_required", "merchant": "Quán Sen", "item": "Phở",
        "category": "Ăn uống", "address": "12 Đường A", "location_lat": None,
        "location_lon": None, "amount": None, "packaging_text": "Bánh đa riêu cua",
        "message": "Đây là gợi ý nhận diện. Hãy kiểm tra và xác nhận trước khi lưu giao dịch.",
    }


def test_image_draft_bad_vision_response_asks_for_manual_review(payments_client, monkeypatch):
    client, _ = payments_client

    class VisionProvider:
        def complete_image(self, system, prompt, image):
            return "not json"

    monkeypatch.setattr(payments, "get_llm_provider", lambda: VisionProvider())
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    response = client.post("/payments/image-draft", files={"file": ("item.png", png, "image/png")})
    assert response.status_code == 200
    assert response.json()["status"] == "manual_review_required"
    assert response.json()["amount"] is None


def test_image_draft_rejects_pdf(payments_client):
    client, _ = payments_client
    response = client.post("/payments/image-draft", files={"file": ("receipt.pdf", b"%PDF-1.4", "application/pdf")})
    assert response.status_code == 415


def test_email_connect_rejects_unapproved_host(payments_client, monkeypatch):
    client, _ = payments_client
    monkeypatch.setenv("IMAP_CREDENTIAL_KEY", "")
    response = client.post("/payments/email-connect", json={"host": "127.0.0.1", "username": "x", "password": "secret"})
    assert response.status_code == 400
    assert "secret" not in response.text


def test_email_connect_requires_encryption_key(payments_client, monkeypatch):
    client, _ = payments_client
    monkeypatch.setenv("IMAP_CREDENTIAL_KEY", "")
    response = client.post("/payments/email-connect", json={"host": "imap.gmail.com", "username": "x", "password": "secret"})
    assert response.status_code == 503
    assert "secret" not in response.text
