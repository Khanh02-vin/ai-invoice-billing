"""Tests cho Phase 3: self-contained billing package.

Dùng :memory: repos + MockProvider để test offline, deterministic.
5+ tests: checkout, entitlements, webhook signature, status transitions, upgrade path.
"""
import json
import hashlib
import hmac
import sqlite3
import tempfile
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from src.billing.models import PlanTier, SubscriptionStatus, Subscription, CheckoutSession, WebhookEvent
from src.billing.provider import MockProvider, MOCK_WEBHOOK_SECRET, get_billing_provider
from src.billing.repository import BillingRepository
from src.billing.entitlements import check_entitlements, ENTITLEMENTS
from src.routers.billing import router as billing_router
from src.store.users import UserRepository
from src.auth.security import hash_password, create_token
from src.domain.models import User
from src.errors import AppError, register_error_handlers


# ---------- Fixtures ----------
@pytest.fixture
def billing_repo():
    """Fresh :memory: billing repo cho mỗi test."""
    return BillingRepository(":memory:")


@pytest.fixture
def user_repo():
    """Fresh :memory: user repo cho mỗi test."""
    return UserRepository(":memory:")


@pytest.fixture
def mock_provider():
    """Fresh MockProvider cho mỗi test."""
    return MockProvider()


@pytest.fixture
def client(billing_repo, user_repo):
    """TestClient với billing router, override module-level repos."""
    from fastapi import FastAPI
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(billing_router)

    import src.routers.billing as billing_mod
    billing_mod.billing_repo = billing_repo
    billing_mod.user_repo = user_repo

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(user_repo):
    """Tạo user + token, trả Authorization headers."""
    created = user_repo.create("billing_tester", hash_password("secret123"))
    user_repo.set_verified(created.id, True)
    token = create_token(user_repo.get_by_username("billing_tester").id)
    return {"Authorization": f"Bearer {token}"}


# ---------- Test 1: Checkout session creation ----------
def test_checkout_session_creation(billing_repo, mock_provider):
    """MockProvider tạo checkout session đúng định dạng."""
    session = mock_provider.create_checkout_session(
        org_id="org_1",
        plan=PlanTier.PRO,
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
        user_id="user_1",
    )

    assert isinstance(session, CheckoutSession)
    assert session.org_id == "org_1"
    assert session.plan == PlanTier.PRO
    assert session.provider == "mock"
    assert session.status == "open"
    assert session.id.startswith("cs_mock_")
    assert "mock://checkout/" in session.url
    assert "plan=pro" in session.url
    assert session.user_id == "user_1"


# ---------- Test 2: Entitlements — free tier blocks ----------
def test_entitlements_free_tier_blocks_actions(billing_repo):
    """Free tier không cho llm_access và pdf_export."""
    # Free tier entitlements
    ent = ENTITLEMENTS[PlanTier.FREE]
    assert ent["invoice_limit"] == 10
    assert ent["llm_access"] is False
    assert ent["pdf_export"] is False

    # check_entitlements trả False cho actions cần quyền
    repo = billing_repo
    repo.upsert_entitlements("org_test", PlanTier.FREE)

    assert check_entitlements("org_test", "llm.access", repo) is False
    assert check_entitlements("org_test", "pdf.export", repo) is False
    # invoice.create được phép vì có limit > 0
    assert check_entitlements("org_test", "invoice.create", repo) is True

    # Pro tier có quyền
    repo.upsert_entitlements("org_pro", PlanTier.PRO)
    assert check_entitlements("org_pro", "llm.access", repo) is True
    assert check_entitlements("org_pro", "pdf.export", repo) is True
    assert check_entitlements("org_pro", "invoice.create", repo) is True


# ---------- Test 3: Webhook signature validation ----------
def test_webhook_signature_validation(mock_provider):
    """MockProvider verify signature đúng, reject sai signature."""
    payload = json.dumps({"type": "customer.subscription.created", "data": {"object": {"id": "sub_123"}}}).encode()

    # Signature đúng
    sig = f"t=1234567890,v1={hmac.new(MOCK_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()}"
    event = mock_provider.handle_webhook(payload, sig)
    assert event.event_type == "customer.subscription.created"
    assert event.processed is False

    # Signature sai
    bad_sig = "t=1234567890,v1=deadbeef"
    with pytest.raises(ValueError, match="Invalid webhook signature"):
        mock_provider.handle_webhook(payload, bad_sig)

    # Payload rỗng
    with pytest.raises(ValueError, match="Invalid webhook"):
        mock_provider.handle_webhook(b"", sig)


def test_webhook_idempotent_sequential_and_rollback(billing_repo):
    event = WebhookEvent(id="evt-duplicate", event_type="test", payload={})
    calls = []

    first = billing_repo.process_webhook_idempotent(event, lambda _: calls.append(1) or {"action": "ok"})
    second = billing_repo.process_webhook_idempotent(event, lambda _: calls.append(1) or {"action": "bad"})
    assert first[1] == "processed"
    assert second[1] == "already_processed"
    assert calls == [1]

    failed = WebhookEvent(id="evt-failed", event_type="test", payload={})
    with pytest.raises(RuntimeError):
        billing_repo.process_webhook_idempotent(failed, lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    assert billing_repo.get_webhook_event(failed.id) is None


def test_webhook_idempotent_concurrent_file_sqlite():
    with tempfile.TemporaryDirectory() as directory:
        path = f"{directory}/billing.sqlite"
        first_repo = BillingRepository(path)
        second_repo = BillingRepository(path)
        event = WebhookEvent(id="evt-concurrent", event_type="test", payload={})
        barrier = threading.Barrier(2)
        results = []

        def worker(repo):
            barrier.wait()
            results.append(repo.process_webhook_idempotent(event, lambda _: {"action": "ok"}))

        threads = [threading.Thread(target=worker, args=(repo,)) for repo in (first_repo, second_repo)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(result[1] for result in results) == ["already_processed", "processed"]


# ---------- Test 4: Subscription status transitions ----------
def test_subscription_status_transitions(billing_repo, mock_provider):
    """Webhook events chuyển trạng thái subscription."""
    # 1. Tạo subscription qua webhook checkout.session.completed
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_123",
                "subscription_id": "sub_prov_1",
                "plan_id": "pro",
                "status": "active",
                "user_id": "user_1",
                "org_id": "org_1",
                "current_period_start": "2026-09-01T00:00:00Z",
                "current_period_end": "2026-10-01T00:00:00Z",
            }
        },
    }
    raw = json.dumps(payload).encode()
    sig = f"t=1,v1={hmac.new(MOCK_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()}"
    event = mock_provider.handle_webhook(raw, sig)

    billing_repo.store_webhook_event(event)
    assert event.processed is False

    # Lưu subscription (router logic)
    user_id = payload["data"]["object"]["user_id"]
    plan_id = payload["data"]["object"]["plan_id"]
    sub = Subscription(
        id="sub_" + uuid.uuid4().hex[:16],
        user_id=user_id,
        org_id=payload["data"]["object"]["org_id"],
        plan=PlanTier(plan_id),
        status=SubscriptionStatus.ACTIVE,
        current_period_start=payload["data"]["object"]["current_period_start"],
        current_period_end=payload["data"]["object"]["current_period_end"],
        provider="mock",
        provider_subscription_id=payload["data"]["object"]["subscription_id"],
    )
    billing_repo.create_subscription(sub)

    # Kiểm tra active
    sub_loaded = billing_repo.get_subscription_by_user(user_id)
    assert sub_loaded is not None
    assert sub_loaded.status == SubscriptionStatus.ACTIVE

    # 2. Webhook past_due
    payload2 = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_prov_1",
                "status": "past_due",
                "user_id": "user_1",
                "plan_id": "pro",
            }
        },
    }
    raw2 = json.dumps(payload2).encode()
    sig2 = f"t=2,v1={hmac.new(MOCK_WEBHOOK_SECRET.encode(), raw2, hashlib.sha256).hexdigest()}"
    event2 = mock_provider.handle_webhook(raw2, sig2)
    billing_repo.update_subscription(sub_loaded.id, status=SubscriptionStatus.PAST_DUE)
    sub_past = billing_repo.get_subscription(sub_loaded.id)
    assert sub_past.status == SubscriptionStatus.PAST_DUE

    # 3. Webhook canceled
    billing_repo.update_subscription(sub_loaded.id, status=SubscriptionStatus.CANCELED)
    sub_cancel = billing_repo.get_subscription(sub_loaded.id)
    assert sub_cancel.status == SubscriptionStatus.CANCELED


# ---------- Test 5: Plan upgrade path ----------
def test_plan_upgrade_path(billing_repo, mock_provider):
    """Webhook cập nhật từ free -> pro -> enterprise."""
    user_id = "user_upgrade"
    org_id = "org_upgrade"

    def make_sub(plan: PlanTier, status: SubscriptionStatus = SubscriptionStatus.ACTIVE):
        return Subscription(
            id=f"sub_{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            org_id=org_id,
            plan=plan,
            status=status,
            current_period_start="2026-09-01T00:00:00Z",
            current_period_end="2026-10-01T00:00:00Z",
            provider="mock",
            provider_subscription_id=f"sub_prov_{uuid.uuid4().hex[:8]}",
        )

    # Free -> Pro
    sub_free = make_sub(PlanTier.FREE)
    billing_repo.create_subscription(sub_free)
    billing_repo.upsert_entitlements(org_id, PlanTier.FREE)

    # Webhook upgrade to pro
    billing_repo.update_subscription(sub_free.id, plan=PlanTier.PRO)
    billing_repo.upsert_entitlements(org_id, PlanTier.PRO, invoice_limit=100, llm_access=True, pdf_export=True)

    ent_pro = billing_repo.get_entitlements(org_id)
    assert ent_pro["plan"] == "pro"
    assert ent_pro["invoice_limit"] == 100
    assert ent_pro["llm_access"] is True
    assert ent_pro["pdf_export"] is True

    # Pro -> Enterprise
    sub_pro = billing_repo.get_subscription_by_user(user_id)
    assert sub_pro is not None
    billing_repo.update_subscription(sub_pro.id, plan=PlanTier.ENTERPRISE)
    billing_repo.upsert_entitlements(org_id, PlanTier.ENTERPRISE, invoice_limit=10000, llm_access=True, pdf_export=True)

    ent_ent = billing_repo.get_entitlements(org_id)
    assert ent_ent["plan"] == "enterprise"
    assert ent_ent["invoice_limit"] == 10000
    assert ent_ent["llm_access"] is True
    assert ent_ent["pdf_export"] is True

    # Enterprise -> Free (downgrade)
    billing_repo.update_subscription(sub_pro.id, plan=PlanTier.FREE)
    billing_repo.upsert_entitlements(org_id, PlanTier.FREE)

    ent_final = billing_repo.get_entitlements(org_id)
    assert ent_final["plan"] == "free"
    assert ent_final["invoice_limit"] == 10
    assert ent_final["llm_access"] is False
    assert ent_final["pdf_export"] is False


# ---------- Test 6: Router integration — checkout + entitlements ----------
def test_router_checkout_and_entitlements(client, auth_headers):
    """Integration: tạo checkout qua router, lấy entitlements."""
    resp = client.post(
        "/billing/checkout",
        json={"plan": "pro", "org_id": "org_1", "success_url": "https://ok", "cancel_url": "https://no"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["plan"] == "pro"
    assert "id" in data
    assert "url" in data
    assert data["status"] == "open"

    # Entitlements endpoint
    resp2 = client.get("/billing/entitlements", headers=auth_headers)
    assert resp2.status_code == 200, resp2.text
    ent = resp2.json()
    assert "plan" in ent
    assert "invoice_limit" in ent


# ---------- Test 7: Webhook router stores event ----------
def test_router_webhook_stores_event(client, billing_repo):
    """Router webhook lưu event vào DB."""
    payload = {
        "type": "customer.subscription.created",
        "data": {"object": {"id": "sub_x", "status": "active", "user_id": "u1", "plan_id": "pro"}},
    }
    raw = json.dumps(payload).encode()
    sig = f"t=1,v1={hmac.new(MOCK_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()}"
    headers = {"Content-Type": "application/json", "stripe-signature": sig}
    resp = client.post("/billing/webhook/mock", content=raw, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "processed"
    assert "event_id" in body


def test_webhook_invalid_signature_rejected(client):
    """Webhook with bad signature returns 400."""
    payload = {"type": "x", "data": {"object": {"id": "y"}}}
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "stripe-signature": "t=1,v1=badsig"}
    resp = client.post("/billing/webhook/mock", content=raw, headers=headers)
    # Bad signature should be rejected with 400
    assert resp.status_code == 400, resp.text
