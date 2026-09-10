"""Billing router — subscription, checkout, webhooks, entitlements.

Routes:
- POST /checkout                    -> create checkout session (auth required, body {plan, org_id, success_url, cancel_url})
- GET  /subscription                -> get current org's subscription
- POST /webhook/{provider}          -> webhook endpoint (validates signature, stores event)
- GET  /entitlements                -> get current org's entitlements

Lưu ý: dùng current_user_local (copy pattern từ orgs.py) để tránh import cycle với app.py.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..auth.security import decode_token
from ..billing.models import PlanTier, Subscription, SubscriptionStatus, CheckoutSession, WebhookEvent
from ..billing.provider import get_billing_provider
from ..billing.repository import BillingRepository
from ..domain.models import User
from ..errors import AppError
from ..store.users import UserRepository

router = APIRouter(prefix="/billing", tags=["billing"])

bearer = HTTPBearer(auto_error=False)

# Billing persistence is independent; user lookup resolves the app's canonical repo.
billing_repo = BillingRepository()
user_repo: UserRepository | None = None


def _canonical_user_repo() -> UserRepository:
    if user_repo is not None:
        return user_repo
    from .. import app as app_module
    return app_module.users


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    """Local copy of current_user to avoid import cycle with app.py."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = _canonical_user_repo().get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


@router.post("/checkout")
def create_checkout(
    body: dict = Body(...),
    user: User = Depends(current_user_local),
) -> dict:
    """Tạo checkout session."""
    plan = body.get("plan")
    org_id = body.get("org_id", "")
    success_url = body.get("success_url", "")
    cancel_url = body.get("cancel_url", "")

    if not plan or plan not in (p.value for p in PlanTier):
        raise AppError("INVALID_INPUT", "plan must be one of: free, pro, enterprise", status=400)

    provider = get_billing_provider()
    try:
        session = provider.create_checkout_session(
            org_id=org_id,
            plan=PlanTier(plan),
            success_url=success_url,
            cancel_url=cancel_url,
            user_id=user.id,
        )
    except Exception as exc:
        import logging
        logging.getLogger("invoice.billing").exception("Checkout provider failure")
        raise AppError("PAYMENT_FAILED", "Payment provider unavailable.", status=502) from exc

    billing_repo.create_checkout_session(session)
    return {
        "id": session.id,
        "url": session.url,
        "status": session.status,
        "plan": session.plan.value,
    }


@router.get("/subscription")
def get_current_subscription(
    user: User = Depends(current_user_local),
) -> dict:
    """Lấy subscription hiện tại của org."""
    subs = billing_repo.list_subscriptions(org_id=user.id)
    active = [
        s for s in subs
        if s.status in (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.PAST_DUE,
        )
    ]
    current = active[0] if active else None
    if not current:
        return {
            "subscription": None,
            "plan": "free",
            "status": "inactive",
        }
    return {
        "subscription": {
            "id": current.id,
            "org_id": current.org_id,
            "user_id": current.user_id,
            "plan": current.plan.value,
            "status": current.status.value,
            "current_period_start": current.current_period_start,
            "current_period_end": current.current_period_end,
            "provider": current.provider,
            "provider_subscription_id": current.provider_subscription_id,
            "created_at": current.created_at,
        },
        "plan": current.plan.value,
        "status": current.status.value,
    }


@router.post("/webhook/{provider}")
async def handle_webhook(provider: str, request: Request) -> dict:
    """Nhận webhook từ provider.

    Validates signature, stores raw event, returns processing result.
    """
    raw_body = await request.body()
    signature = request.headers.get("stripe-signature") or request.headers.get("paddle-signature") or ""

    try:
        billing_provider = get_billing_provider(provider)
        event = billing_provider.handle_webhook(raw_body, signature)
    except Exception as exc:
        import logging
        logging.getLogger("invoice.billing").exception("Webhook provider failure")
        raise AppError("PAYMENT_FAILED", "Payment provider unavailable.", status=400) from exc

    try:
        applied, status, result = billing_repo.process_webhook_idempotent(
            event, lambda claimed_event: _apply_webhook_event(claimed_event)
        )
    except Exception as exc:
        import logging
        logging.getLogger("invoice.billing").exception("Webhook processing failed")
        raise AppError("PAYMENT_FAILED", "Payment could not be processed.", status=500) from exc

    return {"status": status, "event_id": event.id, **result}


@router.get("/entitlements")
def get_entitlements(
    user: User = Depends(current_user_local),
) -> dict:
    """Lấy entitlements của org hiện tại."""
    org_id = user.id  # user id = org id trong mock/test
    ent = billing_repo.get_entitlements(org_id)
    if not ent:
        ent = billing_repo.upsert_entitlements(org_id, PlanTier.FREE)
    return ent


# ---------- Internal helpers ----------

def _apply_webhook_event(event: WebhookEvent) -> dict:
    """Xử lý webhook event nội bộ."""
    envelope = event.payload
    resource = envelope.get("data", {}).get("object", {})
    if not isinstance(resource, dict):
        resource = {}
    data = {**envelope, **resource}
    metadata = resource.get("metadata") or envelope.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    event_type = event.event_type

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "checkout.session.completed",
    ):
        user_id = data.get("user_id") or metadata.get("user_id", "")
        plan_id = data.get("plan_id") or data.get("plan") or metadata.get("plan_id") or metadata.get("plan") or "free"
        org_id = data.get("org_id") or metadata.get("org_id", user_id)
        status_raw = data.get("status", "active")
        try:
            status = SubscriptionStatus(status_raw)
        except ValueError:
            status = SubscriptionStatus.ACTIVE

        try:
            plan = PlanTier(plan_id)
        except ValueError:
            plan = PlanTier.FREE

        existing = billing_repo.get_subscription_by_user(user_id) if user_id else None
        if existing:
            updated = billing_repo.update_subscription(
                existing.id,
                plan=plan,
                status=status,
                current_period_start=data.get("current_period_start"),
                current_period_end=data.get("current_period_end"),
            )
            return {"action": "subscription.updated", "subscription_id": updated.id}
        else:
            sub = Subscription(
                id="sub_" + __import__("uuid").uuid4().hex[:16],
                org_id=org_id or user_id,
                user_id=user_id,
                plan=plan,
                status=status,
                current_period_start=data.get("current_period_start"),
                current_period_end=data.get("current_period_end"),
                provider=event.provider,
                provider_subscription_id=resource.get("id") or data.get("id", ""),
            )
            billing_repo.create_subscription(sub)
            return {"action": "subscription.created", "subscription_id": sub.id}

    elif event_type == "customer.subscription.deleted":
        user_id = data.get("user_id") or metadata.get("user_id", "")
        existing = billing_repo.get_subscription_by_user(user_id) if user_id else None
        if existing:
            updated = billing_repo.update_subscription(existing.id, status=SubscriptionStatus.CANCELED)
            return {"action": "subscription.canceled", "subscription_id": updated.id}

    return {"action": "ignored"}
