"""Tests cho Web Push (PWA): subscribe/unsubscribe + gửi push khi chờ hóa đơn."""
import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src import push as push_module
from src.domain.payments import PaymentTransaction, TxDirection, TxStatus
from src.payments.notify import send_receipt_reminder
from src.store.push import PushSubscriptionRepository
from src.store.repository import InvoiceRepository
from src.store.transactions import TransactionRepository
from src.store.users import UserRepository

VAPID_PUB = "BNVBzrWUcCA7GOOCUOgBWB27-LGwKMXezWFV3qEEayho18hxANT5fLFRvIGkPVh0fLezbcelsw5UYRadsQKGV1Q"
SUB = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/abc-123",
    "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQt", "auth": "tBHItJI5svbpez7KI4CCXg"},
}


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    # Mặc định TẮT push — .env dev có thể đã set VAPID, test nào cần thì setenv lại
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)
    push_repo = PushSubscriptionRepository(db_path=":memory:")
    app_module.repo = InvoiceRepository(db_path=":memory:")
    app_module.users = UserRepository(db_path=":memory:")
    app_module.tx_repo = TransactionRepository(db_path=":memory:")
    app_module.push_repo = push_repo
    push_module.set_repo(push_repo)
    yield
    push_module.set_repo(None)


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username="pushu", password="matkhau123") -> str:
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- API: vapid key + subscribe/unsubscribe ----------

def test_vapid_key_requires_auth(client):
    assert client.get("/push/vapid-public-key").status_code == 401


def test_vapid_key_reports_enabled_when_configured(monkeypatch, client):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", VAPID_PUB)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "./vapid_private.pem")
    token = _register(client)
    r = client.get("/push/vapid-public-key", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "key": VAPID_PUB}


def test_subscribe_upserts_then_unsubscribe(client):
    token = _register(client)
    user = app_module.users.get_by_username("pushu")

    assert client.post("/push/subscribe", json=SUB, headers=_auth(token)).json()["ok"] is True
    # subscribe lại cùng endpoint -> cập nhật key, không nhân đôi
    updated = {**SUB, "keys": {**SUB["keys"], "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQt"}}
    client.post("/push/subscribe", json=updated, headers=_auth(token))
    subs = app_module.push_repo.list_for_user(user.id)
    assert len(subs) == 1 and subs[0]["endpoint"] == SUB["endpoint"]

    r = client.post("/push/unsubscribe", json={"endpoint": SUB["endpoint"]}, headers=_auth(token))
    assert r.json() == {"ok": True, "removed": 1}
    assert app_module.push_repo.list_for_user(user.id) == []


def test_subscriptions_are_user_scoped(client):
    token_a = _register(client, "usera")
    token_b = _register(client, "userb")
    user_a = app_module.users.get_by_username("usera")

    client.post("/push/subscribe", json=SUB, headers=_auth(token_a))
    # B không xoá được subscription của A
    r = client.post("/push/unsubscribe", json={"endpoint": SUB["endpoint"]}, headers=_auth(token_b))
    assert r.json()["removed"] == 0
    assert len(app_module.push_repo.list_for_user(user_a.id)) == 1


# ---------- Gửi push ----------

def test_push_skipped_when_vapid_not_configured(monkeypatch):
    calls: list = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw))
    # Fixture autouse đã xoá VAPID env -> không gửi dù có subscription
    assert push_module.send_push_to_user("u1", "Tiêu đề", "Nội dung") == 0
    assert calls == []


def test_receipt_reminder_sends_push_when_tx_pending(monkeypatch, client):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", VAPID_PUB)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "./vapid_private.pem")
    calls: list = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw))

    token = _register(client)
    user = app_module.users.get_by_username("pushu")
    app_module.push_repo.save(user.id, SUB)

    tx = PaymentTransaction(
        user_id=user.id, amount=350000, currency="VND",
        direction=TxDirection.DEBIT, merchant="WINMART", status=TxStatus.PENDING_RECEIPT,
    )
    send_receipt_reminder(user, tx)

    assert len(calls) == 1
    assert calls[0]["subscription_info"]["endpoint"] == SUB["endpoint"]
    import json
    payload = json.loads(calls[0]["data"])
    assert "350,000 VND" in payload["title"] and "Chụp hóa đơn" in payload["title"]
    assert "Mở app" in payload["body"]
    assert payload["url"] == "/"
    assert calls[0]["vapid_private_key"] == "./vapid_private.pem"
    assert calls[0]["vapid_claims"]["sub"].startswith("mailto:")  # set trong .env


def test_expired_subscription_deleted_on_410(monkeypatch, client):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", VAPID_PUB)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "./vapid_private.pem")

    class Gone(Exception):
        def __init__(self):
            self.response = type("R", (), {"status_code": 410})()

    def fake_webpush(**kw):
        raise Gone()

    monkeypatch.setattr(push_module, "webpush", fake_webpush)
    token = _register(client)
    user = app_module.users.get_by_username("pushu")
    app_module.push_repo.save(user.id, SUB)

    assert push_module.send_push_to_user(user.id, "t", "b") == 0
    # 410 = thiết bị đã gỡ -> xoá để không thử lại
    assert app_module.push_repo.list_for_user(user.id) == []


def test_push_test_endpoint(monkeypatch, client):
    token = _register(client)
    # Fixture autouse xoá VAPID env -> chưa cấu hình thì 503
    r = client.post("/push/test", headers=_auth(token))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "PUSH_DISABLED"

    monkeypatch.setenv("VAPID_PUBLIC_KEY", VAPID_PUB)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "./vapid_private.pem")
    calls: list = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw))
    user = app_module.users.get_by_username("pushu")
    app_module.push_repo.save(user.id, SUB)

    r = client.post("/push/test", headers=_auth(token))
    assert r.status_code == 200 and r.json()["sent"] == 1
