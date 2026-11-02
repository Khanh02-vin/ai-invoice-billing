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
    assert saved["source_file"] == ""


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
    assert saved["mime_type"] == "image/png"
    assert saved["file_size"] == len(PNG)
    assert saved["file_checksum"]
    assert (tmp_path / "storage" / saved["source_file"]).exists()


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


def test_delete_purchase_is_owner_scoped_and_cleans_storage(purchases_client):
    client, db, tmp_path = purchases_client
    created = client.post(
        "/payments/purchases/manual",
        data={"item": "Có ảnh", "amount": 7},
        files={"file": ("anh.png", PNG, "image/png")},
    ).json()
    source_file = client.get("/payments/purchases").json()[0]["source_file"]
    stored = tmp_path / "storage" / source_file
    assert stored.exists()

    _act_as(client, "other")
    assert client.delete(f"/payments/purchases/{created['id']}").status_code == 404

    _act_as(client, "owner")
    assert client.delete(f"/payments/purchases/{created['id']}").status_code == 204
    assert not stored.exists()
    assert client.get("/payments/purchases").json() == []
    assert client.delete(f"/payments/purchases/{created['id']}").status_code == 404
