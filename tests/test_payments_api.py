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


def test_camera_upload_rejects_empty_extraction(payments_client, monkeypatch, tmp_path):
    """OCR không đọc ra field nào thì không lưu invoice 'unknown' như thành công."""
    from types import SimpleNamespace
    from io import BytesIO
    from PIL import Image
    from sqlmodel import select

    client, db = payments_client
    monkeypatch.setattr(
        payments,
        "get_settings",
        lambda: SimpleNamespace(
            storage_dir=str(tmp_path / "storage"),
            max_upload_bytes=2_000_000,
            allowed_mime_types=["image/png"],
            max_pdf_pages=10,
        ),
    )
    monkeypatch.setattr(
        payments,
        "extract_invoice",
        lambda **kwargs: Invoice(
            id="unreadable", user_id="owner", invoice_number="unknown", vendor="unknown",
            total=0, currency="USD"
        ),
    )
    buf = BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")

    response = client.post(
        "/payments/camera-upload",
        files={"file": ("receipt.png", buf.getvalue(), "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXTRACTION_FAILED"
    with db.get_session() as session:
        assert not session.exec(select(Invoice)).all()
    assert not list((tmp_path / "storage").glob("**/*"))


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
    assert linked.json()["id"]
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


def test_manual_link_reconciles_full_payment_and_unlink_reopens_records(payments_client):
    client, db = payments_client
    from src.models import InvoiceStatus, TxStatus
    from src.store.sql_repository import InvoiceRepository, InvoiceTransactionLinkRepository

    invoice = Invoice(id="reconcile-invoice", user_id="owner", invoice_number="I-R", total=100)
    InvoiceRepository(db).upsert(invoice)
    tx = client.post("/payments/transactions", json={"amount": 100}).json()

    linked = client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": invoice.id},
    )
    assert linked.status_code == 200
    refreshed_tx = TransactionRepository(db).get(tx["id"], "owner")
    refreshed_invoice = InvoiceRepository(db).get(invoice.id, user_id="owner")
    assert refreshed_tx.status == TxStatus.MATCHED
    assert refreshed_tx.matched_invoice_id == invoice.id
    assert refreshed_invoice.status == InvoiceStatus.PAID

    unlinked = client.delete(f"/payments/transactions/{tx['id']}/invoices/{invoice.id}")
    assert unlinked.status_code == 200
    refreshed_tx = TransactionRepository(db).get(tx["id"], "owner")
    refreshed_invoice = InvoiceRepository(db).get(invoice.id, user_id="owner")
    assert refreshed_tx.status == TxStatus.PENDING_RECEIPT
    assert refreshed_tx.matched_invoice_id is None
    assert refreshed_invoice.status == InvoiceStatus.UNPAID
    assert not InvoiceTransactionLinkRepository(db).list_for_transaction(tx["id"], "owner")


def test_manual_partial_links_cannot_overallocate_transaction_or_invoice(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    invoices = InvoiceRepository(db)
    invoices.upsert(Invoice(id="partial-a", user_id="owner", invoice_number="P-A", total=80))
    invoices.upsert(Invoice(id="partial-b", user_id="owner", invoice_number="P-B", total=80))
    tx = client.post("/payments/transactions", json={"amount": 100}).json()

    first = client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": "partial-a", "amount": 60},
    )
    assert first.status_code == 200
    assert client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": "partial-b", "amount": 50},
    ).status_code == 400

    second = client.post(
        f"/payments/transactions/{tx['id']}/invoices",
        json={"invoice_id": "partial-a", "amount": 30},
    )
    assert second.status_code == 400


def test_manual_link_rejects_dismissed_and_credit_transactions(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    InvoiceRepository(db).upsert(Invoice(id="eligible", user_id="owner", invoice_number="ELIG", total=50))
    dismissed = client.post("/payments/transactions", json={"amount": 50}).json()
    assert client.post(f"/payments/transactions/{dismissed['id']}/dismiss").status_code == 200
    credit = client.post(
        "/payments/transactions", json={"amount": 50, "direction": "credit"}
    ).json()

    for tx in (dismissed, credit):
        response = client.post(
            f"/payments/transactions/{tx['id']}/invoices",
            json={"invoice_id": "eligible"},
        )
        assert response.status_code == 409


def test_transaction_invoice_candidates_report_unique_and_ambiguous_without_verifying_payment(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    invoices = InvoiceRepository(db)
    invoices.upsert(Invoice(id="candidate-a", user_id="owner", invoice_number="CA", total=100, currency="VND"))
    invoices.upsert(Invoice(id="candidate-b", user_id="owner", invoice_number="CB", total=100, currency="VND"))
    tx = client.post("/payments/transactions", json={"amount": 100, "currency": "VND"}).json()

    response = client.get(f"/payments/transactions/{tx['id']}/invoice-candidates")
    assert response.status_code == 200
    assert response.json()["match_status"] == "ambiguous"
    assert {item["id"] for item in response.json()["candidates"]} == {"candidate-a", "candidate-b"}
    assert response.json()["payment_verified"] is False

    assert client.get("/payments/transactions/missing/invoice-candidates").status_code == 404


def test_transaction_invoice_candidates_reload_states_unique_ambiguous_none(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    invoices = InvoiceRepository(db)
    # Create the transaction first so it stays pending, then add the matching
    # invoice — this is the state the candidate panel reads before linking.
    unique_tx = client.post("/payments/transactions", json={"amount": 700000, "currency": "VND"}).json()
    invoices.upsert(Invoice(id="unique-inv", user_id="owner", invoice_number="U-1", total=700000, currency="VND"))

    # ``unique`` is visible on a fresh refetch before any link state exists.
    candidates = client.get(f"/payments/transactions/{unique_tx['id']}/invoice-candidates")
    assert candidates.json()["match_status"] == "unique"
    assert [item["id"] for item in candidates.json()["candidates"]] == ["unique-inv"]
    assert candidates.json()["payment_verified"] is False

    # Link from the candidate, then refetch through the API: the durable state
    # is what survives a page reload.
    assert client.post(f"/payments/transactions/{unique_tx['id']}/invoices", json={"invoice_id": "unique-inv"}).status_code == 200
    refreshed = client.get("/payments/transactions").json()
    assert next(tx for tx in refreshed if tx["id"] == unique_tx["id"])["status"] == "matched"
    assert next(tx for tx in refreshed if tx["id"] == unique_tx["id"])["matched_invoice_id"] == "unique-inv"

    # Unlink, then refetch: pending state must be restored.
    assert client.delete(f"/payments/transactions/{unique_tx['id']}/invoices/unique-inv").status_code == 200
    refreshed = client.get("/payments/transactions").json()
    assert next(tx for tx in refreshed if tx["id"] == unique_tx["id"])["status"] == "pending_receipt"
    assert next(tx for tx in refreshed if tx["id"] == unique_tx["id"])["matched_invoice_id"] is None

    invoices.upsert(Invoice(id="amb-a", user_id="owner", invoice_number="A-1", total=900000, currency="VND"))
    invoices.upsert(Invoice(id="amb-b", user_id="owner", invoice_number="A-2", total=900000, currency="VND"))
    ambiguous_tx = client.post("/payments/transactions", json={"amount": 900000, "currency": "VND"}).json()
    candidates = client.get(f"/payments/transactions/{ambiguous_tx['id']}/invoice-candidates")
    assert candidates.json()["match_status"] == "ambiguous"
    assert {item["id"] for item in candidates.json()["candidates"]} == {"amb-a", "amb-b"}
    assert {item["total"] for item in candidates.json()["candidates"]} == {900000}
    assert candidates.json()["payment_verified"] is False

    none_tx = client.post("/payments/transactions", json={"amount": 7654321, "currency": "VND"}).json()
    candidates = client.get(f"/payments/transactions/{none_tx['id']}/invoice-candidates")
    assert candidates.json()["match_status"] == "none"
    assert candidates.json()["candidates"] == []
    assert candidates.json()["payment_verified"] is False


def test_partial_link_transitions_survive_refetch_and_unlink(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    invoices = InvoiceRepository(db)
    invoices.upsert(Invoice(id="partial-inv", user_id="owner", invoice_number="P-1", total=100000, currency="VND"))
    tx = client.post("/payments/transactions", json={"amount": 150000, "currency": "VND"}).json()

    assert client.post(f"/payments/transactions/{tx['id']}/invoices", json={"invoice_id": "partial-inv", "amount": 60000}).status_code == 200
    refreshed = client.get("/payments/transactions").json()
    assert next(item for item in refreshed if item["id"] == tx["id"])["status"] == "pending_receipt"

    candidates = client.get(f"/payments/transactions/{tx['id']}/invoice-candidates")
    assert candidates.json()["allocated_amount"] == 60000
    assert candidates.json()["remaining_amount"] == 90000
    assert candidates.json()["payment_verified"] is False

    assert client.delete(f"/payments/transactions/{tx['id']}/invoices/partial-inv").status_code == 200
    candidates = client.get(f"/payments/transactions/{tx['id']}/invoice-candidates")
    assert candidates.json()["allocated_amount"] == 0
    assert candidates.json()["remaining_amount"] == 150000


def test_transaction_create_validates_amount_and_location_pair(payments_client):
    client, _ = payments_client
    assert client.post("/payments/transactions", json={"amount": -1}).status_code == 422
    assert client.post("/payments/transactions", json={"amount": 5, "location_lat": 10}).status_code == 422


def test_update_linked_transaction_rejects_amount_or_direction_that_breaks_allocations(payments_client):
    client, db = payments_client
    from src.store.sql_repository import InvoiceRepository

    invoice = Invoice(id="edit-linked", user_id="owner", invoice_number="EDIT-LINK", total=100)
    InvoiceRepository(db).upsert(invoice)
    tx = client.post("/payments/transactions", json={"amount": 100}).json()
    assert client.post(
        f"/payments/transactions/{tx['id']}/invoices", json={"invoice_id": invoice.id}
    ).status_code == 200

    increased = client.put(f"/payments/transactions/{tx['id']}", json={"amount": 150})
    assert increased.status_code == 200
    assert increased.json()["status"] == "pending_receipt"

    reduced = client.put(f"/payments/transactions/{tx['id']}", json={"amount": 80})
    assert reduced.status_code == 409
    changed_direction = client.put(
        f"/payments/transactions/{tx['id']}", json={"direction": "credit"}
    )
    assert changed_direction.status_code == 409


def test_sharing_requires_opt_in_and_opt_out_deletes_visits(payments_client):
    client, db = payments_client
    place = {"name": "Cafe", "kind": "cafe", "lat": 10.0, "lon": 106.0, "confirmed": True}

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


def test_transaction_location_fields_round_trip_without_conflating_merchant_location(payments_client):
    client, _ = payments_client
    created = client.post("/payments/transactions", json={
        "amount": 42,
        "payment_lat": 0,
        "payment_lon": 106.7,
        "payment_captured_at": "2026-11-08T10:00:00Z",
        "payment_accuracy_m": 8.5,
        "payment_source": "gps",
        "merchant_lat": 10.77,
        "merchant_lon": 106.7,
        "merchant_name": "Quán Sen",
        "merchant_source": "user_input",
        "merchant_verified_by_user": True,
        "purchase_context_type": "physical",
        "user_confirmed_location": True,
    })
    assert created.status_code == 200
    tx_id = created.json()["id"]
    assert created.json()["payment_lat"] == 0
    assert created.json()["merchant_lat"] == 10.77
    assert created.json()["merchant_verified_by_user"] is True
    assert created.json()["purchase_context_type"] == "physical"

    updated = client.put(f"/payments/transactions/{tx_id}", json={
        "merchant_address": "12 Nguyễn Huệ",
        "evidence_refs": ["attachment-1"],
    })
    assert updated.status_code == 200
    assert updated.json()["payment_lat"] == 0
    assert updated.json()["merchant_address"] == "12 Nguyễn Huệ"
    assert updated.json()["evidence_refs"] == ["attachment-1"]


def test_transaction_location_pair_validation_applies_to_new_coordinates(payments_client):
    client, _ = payments_client
    response = client.post("/payments/transactions", json={
        "amount": 42,
        "payment_lat": 10,
    })
    assert response.status_code == 422

    legacy = client.post("/payments/transactions", json={
        "amount": 875, "location_lat": 10, "location_lon": 106,
    }).json()
    updated = client.put(f"/payments/transactions/{legacy['id']}", json={"note": "Mua đồ"})
    assert updated.status_code == 200
    assert updated.json()["amount"] == 875
    assert updated.json()["location_lat"] == 10
    assert updated.json()["location_lon"] == 106


def test_timeline_prefers_payment_coordinates_and_falls_back_to_legacy(payments_client):
    client, _ = payments_client
    preferred = client.post("/payments/transactions", json={
        "amount": 10,
        "payment_lat": 0,
        "payment_lon": 106,
        "merchant_lat": 10.7,
        "merchant_lon": 106.6,
        "location_lat": 11,
        "location_lon": 107,
    }).json()
    legacy = client.post("/payments/transactions", json={
        "amount": 20,
        "location_lat": 12,
        "location_lon": 108,
        "merchant_lat": 13,
        "merchant_lon": 109,
    }).json()

    response = client.get("/payments/timeline")

    assert response.status_code == 200
    points = {point["id"]: point for point in response.json()["points"]}
    assert (points[preferred["id"]]["lat"], points[preferred["id"]]["lon"]) == (0, 106)
    assert (points[legacy["id"]]["lat"], points[legacy["id"]]["lon"]) == (12, 108)


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
