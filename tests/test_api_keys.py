"""API keys bên thứ ba — tạo/quản lý key + xác thực X-API-Key theo scope + rate limit."""
import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.store.sql_repository import (
    ApiKeyRepository,
    Database,
    InvoiceRepository,
    TransactionRepository,
    UserRepository,
)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    app_db = Database(str(tmp_path / "test.db"))
    app_db.create_tables()
    app_module.repo = InvoiceRepository(app_db)
    app_module.users = UserRepository(app_db)
    app_module.tx_repo = TransactionRepository(app_db)
    app_module.api_keys_repo = ApiKeyRepository(app_db)
    try:
        from src.security.rate_limit import get_default_limiter

        get_default_limiter().clear()
    except Exception:
        pass
    yield


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username="apiu", password="matkhau123") -> str:
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_key(client, token, scopes=("invoices:read",), name="Kế toán", **extra) -> dict:
    body = {"name": name, "scopes": list(scopes)}
    body.update(extra)
    r = client.post("/api-keys", json=body, headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Quản lý key ----------

def test_create_list_revoke_key(client):
    """Tạo → plaintext trả 1 lần; list chỉ thấy prefix; revoke → is_active=false."""
    token = _register(client)
    created = _create_key(client, token)
    assert created["key"].startswith("sk_live_")
    assert created["key_prefix"] == created["key"][:12]
    assert created["scopes"] == ["invoices:read"]
    assert "key_hash" not in created

    keys = client.get("/api-keys", headers=_auth(token)).json()
    assert len(keys) == 1
    assert keys[0]["key_prefix"] == created["key_prefix"]
    assert "key" not in keys[0]  # list không trả secret

    assert client.delete(f"/api-keys/{created['id']}", headers=_auth(token)).json() == {"revoked": True}
    keys = client.get("/api-keys", headers=_auth(token)).json()
    assert keys[0]["is_active"] is False


def test_key_management_requires_auth(client):
    """Không JWT → 401; user khác không thấy/xóa được key của user này."""
    token = _register(client, "owner1", "pass123456")
    other = _register(client, "other1", "pass123456")
    assert client.post("/api-keys", json={"name": "x", "scopes": ["invoices:read"]}).status_code == 401

    created = _create_key(client, token)
    keys = client.get("/api-keys", headers=_auth(other)).json()
    assert keys == []
    r = client.delete(f"/api-keys/{created['id']}", headers=_auth(other))
    assert r.status_code == 404


def test_unknown_scope_rejected(client):
    token = _register(client)
    r = client.post("/api-keys", json={"name": "x", "scopes": ["admin:all"]}, headers=_auth(token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_SCOPES"


# ---------- Xác thực X-API-Key ----------

def test_api_key_reads_invoices(client):
    """Key invoices:read → GET /invoices thay JWT, đúng user của key."""
    token = _register(client)
    client.post(
        "/invoices", json={"invoice_number": "INV-K1", "vendor": "Shop", "total": 100.0},
        headers=_auth(token),
    )
    key = _create_key(client, token, scopes=["invoices:read"])

    r = client.get("/invoices", headers={"X-API-Key": key["key"]})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert r.json()[0]["invoice_number"] == "INV-K1"


def test_api_key_scope_denied(client):
    """Key chỉ có invoices:read → POST /invoices bị 403 thiếu scope."""
    token = _register(client)
    key = _create_key(client, token, scopes=["invoices:read"])
    r = client.post("/invoices", json={"invoice_number": "INV-K2"}, headers={"X-API-Key": key["key"]})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_api_key_route_not_exposed(client):
    """Route ngoài SCOPE_RULES (auth, api-keys, push) bị chặn 403 với API key."""
    token = _register(client)
    key = _create_key(client, token, scopes=[
        "invoices:read", "invoices:write", "payments:read", "payments:write", "reports:read",
    ])
    h = {"X-API-Key": key["key"]}
    assert client.get("/auth/me", headers=h).status_code == 403
    assert client.get("/api-keys", headers=h).status_code == 403
    # router JWT-only (push) trả 401 — API key vẫn không chạm được
    assert client.get("/push/vapid-public-key", headers=h).status_code in (401, 403)


def test_api_key_reads_transactions(client):
    """payments:read → GET /payments/transactions (router riêng vẫn nhận key)."""
    from src.domain.payments import TxStatus
    from src.models import PaymentTransaction

    token = _register(client)
    user_id = app_module.users.get_by_username("apiu").id
    app_module.tx_repo.create(PaymentTransaction(
        id="api-tx", user_id=user_id, amount=500000, currency="VND",
        status=TxStatus.PENDING_RECEIPT,
    ))
    key = _create_key(client, token, scopes=["payments:read"])
    r = client.get("/payments/transactions", headers={"X-API-Key": key["key"]})
    assert r.status_code == 200, r.text
    assert any(t["id"] == "api-tx" for t in r.json())


def test_api_key_invalid_revoked_expired(client):
    token = _register(client)
    key = _create_key(client, token, scopes=["invoices:read"])

    # key sai
    assert client.get("/invoices", headers={"X-API-Key": "sk_live_sai"}).status_code == 401
    # không key → vẫn 401 (JWT path)
    assert client.get("/invoices").status_code == 401

    # revoke xong key chết
    client.delete(f"/api-keys/{key['id']}", headers=_auth(token))
    assert client.get("/invoices", headers={"X-API-Key": key["key"]}).status_code == 401

    # key hết hạn
    expired = _create_key(
        client, token, name="exp", expires_at="2020-01-01T00:00:00+00:00"
    )
    assert client.get("/invoices", headers={"X-API-Key": expired["key"]}).status_code == 401


def test_api_key_rate_limit(client):
    """rate_limit_per_min=2 → request thứ 3 trong phút bị 429."""
    token = _register(client)
    key = _create_key(client, token, scopes=["invoices:read"], rate_limit_per_min=2)
    h = {"X-API-Key": key["key"]}
    assert client.get("/invoices", headers=h).status_code == 200
    assert client.get("/invoices", headers=h).status_code == 200
    r = client.get("/invoices", headers=h)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"


def test_jwt_still_works_alongside_api_key(client):
    """JWT không bị ảnh hưởng bởi nhánh API key."""
    token = _register(client)
    assert client.get("/invoices", headers=_auth(token)).status_code == 200
