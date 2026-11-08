import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.models import Purchase, User
from src.routers import payments
from src.store.sql_repository import Database, PurchaseRepository

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


@pytest.fixture
def purchases_client(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "purchases.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add(User(id="owner", username="owner", password_hash="x" * 60, verified=True))
        session.add(User(id="other", username="other", password_hash="y" * 60, verified=True))
        session.commit()
    monkeypatch.setattr(payments, "_get_db", lambda: db)
    # Chặn gọi Nominatim thật trong test; test riêng sẽ đè bằng bản giả
    monkeypatch.setattr(payments, "_reverse_geocode", lambda lat, lon: None)
    app_module.app.dependency_overrides[payments.current_user] = lambda: User(
        id="owner", username="owner", password_hash="x" * 60, verified=True
    )
    yield TestClient(app_module.app), db, tmp_path
    app_module.app.dependency_overrides.clear()


def _act_as(client, user_id="other"):
    app_module.app.dependency_overrides[payments.current_user] = lambda: User(
        id=user_id, username=user_id, password_hash="x" * 60, verified=True
    )


def test_create_manual_purchase_without_file_round_trips(purchases_client):
    client, db, _ = purchases_client
    response = client.post(
        "/payments/purchases/manual",
        data={
            "item": "Bánh mì", "category": "Ăn uống", "merchant": "Tiệm Hùng",
            "amount": 25000, "currency": "VND", "occurred_at": "2026-10-31T09:30:00",
            "notes": "Trả tiền mặt",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["item"] == "Bánh mì"
    assert body["amount"] == 25000
    assert body["currency"] == "VND"

    listed = client.get("/payments/purchases").json()
    assert len(listed) == 1
    saved = listed[0]
    assert saved["merchant"] == "Tiệm Hùng"
    assert saved["notes"] == "Trả tiền mặt"
    assert saved["occurred_at"] == "2026-10-31T09:30:00"
    assert saved["location_captured_at"] is None
    assert saved["photo_available"] is False
    assert "source_file" not in saved


def test_create_manual_purchase_round_trips_distinct_location_context(purchases_client):
    client, _, _ = purchases_client
    response = client.post(
        "/payments/purchases/manual",
        data={
            "item": "Cà phê",
            "amount": 30,
            "payment_lat": 0,
            "payment_lon": 106.7,
            "payment_source": "gps",
            "merchant_lat": 10.77,
            "merchant_lon": 106.7,
            "merchant_name": "Quán Sen",
            "merchant_verified_by_user": "true",
            "purchase_context_type": "physical",
            "user_confirmed_location": "true",
        },
    )
    assert response.status_code == 201
    saved = response.json()
    assert saved["payment_lat"] == 0
    assert saved["merchant_lat"] == 10.77
    assert saved["merchant_verified_by_user"] is True
    assert saved["purchase_context_type"] == "physical"

    listed = client.get("/payments/purchases").json()[0]
    assert listed["payment_lat"] == 0
    assert listed["merchant_lat"] == 10.77
    assert listed["user_confirmed_location"] is True


def test_create_manual_purchase_rejects_incomplete_payment_coordinates(purchases_client):
    client, _, _ = purchases_client
    response = client.post(
        "/payments/purchases/manual",
        data={"item": "Cà phê", "amount": 30, "payment_lat": 10},
    )
    assert response.status_code == 422


def test_create_manual_purchase_records_location_capture_time(purchases_client):
    client, db, _ = purchases_client
    with_gps = client.post(
        "/payments/purchases/manual",
        data={"item": "Cà phê", "amount": 30, "location_lat": 10.77, "location_lon": 106.7,
              "location_address": "12 Nguyễn Huệ"},
    ).json()
    without_gps = client.post(
        "/payments/purchases/manual",
        data={"item": "Sách", "amount": 90},
    ).json()

    listed = {p["item"]: p for p in client.get("/payments/purchases").json()}
    assert listed["Cà phê"]["location_lat"] == 10.77
    assert listed["Cà phê"]["location_captured_at"]
    assert listed["Sách"]["location_captured_at"] is None
    assert listed["Sách"]["location_lat"] is None


def test_create_manual_purchase_fills_address_from_coordinates(purchases_client, monkeypatch):
    client, db, _ = purchases_client
    monkeypatch.setattr(payments, "_reverse_geocode", lambda lat, lon: "12 Nguyễn Huệ, Quận 1, TP.HCM")
    response = client.post(
        "/payments/purchases/manual",
        data={"item": "Cà phê", "amount": 30, "location_lat": 10.77, "location_lon": 106.7},
    )
    assert response.status_code == 201
    assert response.json()["location_address"] == "12 Nguyễn Huệ, Quận 1, TP.HCM"


def test_create_manual_purchase_keeps_user_address_and_skips_geocode(purchases_client, monkeypatch):
    client, db, _ = purchases_client
    calls = []
    monkeypatch.setattr(payments, "_reverse_geocode", lambda lat, lon: calls.append(lat))
    response = client.post(
        "/payments/purchases/manual",
        data={"item": "Sách", "amount": 50, "location_lat": 10.77, "location_lon": 106.7,
              "location_address": "Nhà sách quen"},
    )
    assert response.status_code == 201
    assert response.json()["location_address"] == "Nhà sách quen"
    assert calls == []  # đã có địa chỉ người dùng nhập → không gọi Nominatim


def test_create_manual_purchase_survives_geocode_failure(purchases_client, monkeypatch):
    client, db, _ = purchases_client
    monkeypatch.setattr(payments, "_reverse_geocode", lambda lat, lon: None)
    response = client.post(
        "/payments/purchases/manual",
        data={"item": "Bánh", "amount": 10, "location_lat": 10.77, "location_lon": 106.7},
    )
    assert response.status_code == 201
    assert response.json()["location_address"] is None


def test_reverse_geocode_parses_nominatim(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"display_name": "12 Nguyễn Huệ, Quận 1, TP.HCM"}

    monkeypatch.setattr(payments.httpx, "get", lambda *a, **k: FakeResponse())
    assert payments._reverse_geocode(10.77, 106.7) == "12 Nguyễn Huệ, Quận 1, TP.HCM"


def test_reverse_geocode_returns_none_on_network_error(monkeypatch):
    import httpx as httpx_module

    def boom(*args, **kwargs):
        raise httpx_module.ConnectError("offline")

    monkeypatch.setattr(payments.httpx, "get", boom)
    assert payments._reverse_geocode(10.77, 106.7) is None


def test_create_manual_purchase_with_image_saves_file(purchases_client):
    client, db, tmp_path = purchases_client
    response = client.post(
        "/payments/purchases/manual",
        data={"item": "Dép", "amount": 120},
        files={"file": ("dep.png", PNG, "image/png")},
    )
    assert response.status_code == 201

    saved = client.get("/payments/purchases").json()[0]
    assert saved["photo_available"] is True
    assert "source_file" not in saved
    assert "file_checksum" not in saved
    assert "file_size" not in saved
    assert "mime_type" not in saved
    purchase = PurchaseRepository(db).get(saved["id"], "owner")
    assert (tmp_path / "storage" / purchase.source_file).exists()


def test_create_manual_purchase_rejects_non_image(purchases_client):
    client, db, _ = purchases_client
    response = client.post(
        "/payments/purchases/manual",
        data={"item": "Hóa đơn PDF", "amount": 1},
        files={"file": ("receipt.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "IMAGE_REQUIRED"
    assert client.get("/payments/purchases").json() == []


def test_create_manual_purchase_rejects_negative_amount(purchases_client):
    client, db, _ = purchases_client
    response = client.post("/payments/purchases/manual", data={"item": "X", "amount": -1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_AMOUNT"


def test_purchase_list_is_owner_scoped(purchases_client):
    client, db, _ = purchases_client
    client.post("/payments/purchases/manual", data={"item": "Của tôi", "amount": 10})
    PurchaseRepository(db).create(Purchase(id="foreign", user_id="other", item="Của người khác", amount=5))

    mine = client.get("/payments/purchases").json()
    assert [p["item"] for p in mine] == ["Của tôi"]

    _act_as(client, "other")
    theirs = client.get("/payments/purchases").json()
    assert [p["item"] for p in theirs] == ["Của người khác"]


def test_purchase_list_filters_by_date_range(purchases_client):
    client, db, _ = purchases_client
    client.post("/payments/purchases/manual", data={"item": "Trong khoảng", "amount": 1,
                                                    "occurred_at": "2026-10-15"})
    client.post("/payments/purchases/manual", data={"item": "Trước đó", "amount": 1,
                                                    "occurred_at": "2026-09-01"})

    october = client.get("/payments/purchases", params={"from_date": "2026-10-01", "to_date": "2026-10-31"}).json()
    assert [p["item"] for p in october] == ["Trong khoảng"]

    clamped = client.get("/payments/purchases", params={"limit": 0}).json()
    assert len(clamped) == 1


def test_purchase_photo_content_is_owner_scoped_and_returns_recorded_mime(purchases_client):
    client, _, _ = purchases_client
    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Có ảnh", "amount": 7},
        files={"file": ("anh.png", PNG, "image/png")},
    ).json()

    response = client.get(f"/payments/purchases/{created['id']}/photo")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"

    _act_as(client, "other")
    assert client.get(f"/payments/purchases/{created['id']}/photo").status_code == 404


def test_purchase_photo_content_not_found_without_photo_or_storage_object(purchases_client):
    client, db, tmp_path = purchases_client
    no_photo = PurchaseRepository(db).create(Purchase(user_id="owner", item="No photo", amount=1))
    assert client.get(f"/payments/purchases/{no_photo.id}/photo").status_code == 404

    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Missing object", "amount": 7},
        files={"file": ("anh.png", PNG, "image/png")},
    ).json()
    source_file = PurchaseRepository(db).list("owner")[0].source_file
    (tmp_path / "storage" / source_file).unlink()
    assert client.get(f"/payments/purchases/{created['id']}/photo").status_code == 404


def test_delete_purchase_preserves_record_when_storage_delete_fails(purchases_client, monkeypatch):
    client, db, _ = purchases_client
    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Keep evidence", "amount": 7},
        files={"file": ("anh.png", PNG, "image/png")},
    ).json()

    def fail_delete(self, key):
        raise OSError("storage unavailable")

    monkeypatch.setattr(payments.LocalFileStorage, "delete", fail_delete)
    response = client.delete(f"/payments/purchases/{created['id']}")

    assert response.status_code == 500
    assert PurchaseRepository(db).get(created["id"], "owner") is not None


def test_delete_purchase_is_owner_scoped_and_cleans_storage(purchases_client):
    client, db, tmp_path = purchases_client
    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Có ảnh", "amount": 7},
        files={"file": ("anh.png", PNG, "image/png")},
    ).json()
    source_file = PurchaseRepository(db).list("owner")[0].source_file
    stored = tmp_path / "storage" / source_file
    assert stored.exists()

    _act_as(client, "other")
    assert client.delete(f"/payments/purchases/{created['id']}").status_code == 404

    _act_as(client, "owner")
    assert client.delete(f"/payments/purchases/{created['id']}").status_code == 204
    assert not stored.exists()
    assert client.get("/payments/purchases").json() == []
    assert client.delete(f"/payments/purchases/{created['id']}").status_code == 404
