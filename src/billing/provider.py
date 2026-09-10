"""Billing provider adapter — provider-agnostic interface cho Stripe/Paddle.

Thiết kế:
- AbstractPaymentProvider abstract: create_checkout_session / create_portal_session /
  handle_webhook / get_subscription.
- MockProvider: deterministic, offline, dùng cho dev/test. Lưu subs trong dict,
  validate webhook signature bằng fixed test key.
- StripeProvider: skeleton — raise NotImplementedError nhưng document interface rõ ràng.
- get_billing_provider() factory đọc BILLING_PROVIDER env (mock|stripe|paddle).

Lưu ý: không thêm dep nặng. Stripe lib chỉ import khi cần.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from .models import (
    PlanTier, Subscription, SubscriptionStatus, CheckoutSession, WebhookEvent,
)


# --- Webhook test key (MockProvider) ---
MOCK_WEBHOOK_SECRET = "whsec_test_mock_secret_key_2026"


class AbstractPaymentProvider(ABC):
    """Abstract payment provider — map chuẩn Stripe/Paddle operations.

    Mỗi method đều document rõ ràng để implement cho từng provider.
    """

    @abstractmethod
    def create_checkout_session(
        self,
        org_id: str,
        plan: PlanTier,
        success_url: str,
        cancel_url: str,
        user_id: str = "",
    ) -> CheckoutSession:
        """Tạo checkout session, trả CheckoutSession với URL redirect.

        Args:
            org_id: tổ chức mua.
            plan: gói đăng ký.
            success_url: redirect URL khi thành công.
            cancel_url: redirect URL khi hủy.
            user_id: người khởi tạo.

        Returns:
            CheckoutSession với url để redirect user.
        """
        ...

    @abstractmethod
    def create_portal_session(self, org_id: str) -> str:
        """Tạo customer portal session URL (Stripe Billing Portal / Paddle Customer Portal).

        Args:
            org_id: tổ chức cần quản lý.

        Returns:
            URL redirect đến customer portal.
        """
        ...

    @abstractmethod
    def handle_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """Verify chữ ký webhook và parse event.

        Args:
            payload: raw request body bytes.
            signature: giá trị header chữ ký (Stripe-Signature / Paddle-Signature).

        Returns:
            WebhookEvent đã parse.

        Raises:
            ValueError: nếu signature không hợp lệ.
        """
        ...

    @abstractmethod
    def get_subscription(self, provider_sub_id: str) -> dict:
        """Lấy thông tin subscription từ provider.

        Args:
            provider_sub_id: ID subscription ở provider.

        Returns:
            dict chứa thông tin subscription.
        """
        ...


class MockProvider(AbstractPaymentProvider):
    """Provider giả lập — deterministic, offline, dùng dev/test.

    - Checkout URL dạng mock://checkout/<session_id>.
    - Webhook verify signature bằng HMAC-SHA256 với MOCK_WEBHOOK_SECRET.
    - Lưu subscriptions trong dict nội bộ.
    - Portal URL dạng mock://portal/<org_id>.
    """

    def __init__(self, webhook_secret: str = MOCK_WEBHOOK_SECRET):
        self._webhook_secret = webhook_secret
        self._subscriptions: Dict[str, dict] = {}
        self._checkout_sessions: Dict[str, CheckoutSession] = {}

    def create_checkout_session(
        self,
        org_id: str,
        plan: PlanTier,
        success_url: str,
        cancel_url: str,
        user_id: str = "",
    ) -> CheckoutSession:
        """Tạo mock checkout session — deterministic."""
        session_id = f"cs_mock_{uuid.uuid4().hex[:16]}"
        session = CheckoutSession(
            id=session_id,
            org_id=org_id,
            user_id=user_id,
            plan=plan,
            provider="mock",
            url=f"mock://checkout/{session_id}?plan={plan.value}&success={success_url}&cancel={cancel_url}",
            status="open",
        )
        self._checkout_sessions[session_id] = session
        return session

    def create_portal_session(self, org_id: str) -> str:
        """Tạo mock portal URL."""
        return f"mock://portal/{org_id}"

    def handle_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """Verify mock webhook signature (HMAC-SHA256) và parse event.

        Signature format: "t=<timestamp>,v1=<hmac_sha256>"
        """
        if not self._verify_signature(payload, signature):
            raise ValueError("Invalid webhook signature")

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Invalid webhook payload: {e}") from e

        event_type = data.get("type", "unknown")
        provider_event_id = data.get("id") or data.get("event_id")
        if not isinstance(provider_event_id, str) or not provider_event_id.strip():
            # Legacy mock fixtures may omit an envelope ID; derive a stable digest
            # so retries of identical payloads still remain idempotent.
            provider_event_id = "mock_" + hashlib.sha256(payload).hexdigest()[:32]
        resource = data.get("data", {}).get("object", {})
        if not isinstance(resource, dict):
            raise ValueError("Invalid webhook payload: data.object must be an object")
        provider_sub_id = resource.get("id", "")

        # Lưu subscription mock nếu là event tạo/cập nhật
        if event_type in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "subscription.created",
            "subscription.updated",
        ) and provider_sub_id:
            self._subscriptions[provider_sub_id] = data

        return WebhookEvent(
            id=provider_event_id,
            provider="mock",
            event_type=event_type,
            payload=data,
            processed=False,
        )

    def get_subscription(self, provider_sub_id: str) -> dict:
        """Lấy mock subscription từ dict nội bộ."""
        if provider_sub_id in self._subscriptions:
            return self._subscriptions[provider_sub_id]
        return {
            "id": provider_sub_id,
            "status": "active",
            "plan": "mock_plan",
            "provider": "mock",
        }

    def _verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify HMAC-SHA256 signature.

        Format: "t=<timestamp>,v1=<expected_hmac>"
        """
        if not signature or not payload:
            return False
        try:
            expected = hmac.new(
                self._webhook_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()
            # Tìm trường v1= trong signature
            parts = {}
            for part in signature.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    parts[k.strip()] = v.strip()
            return hmac.compare_digest(parts.get("v1", ""), expected)
        except Exception:
            return False


class StripeProvider(AbstractPaymentProvider):
    """Stripe provider — skeleton.

    Chưa implement: cần thêm `stripe` package và STRIPE_SECRET_KEY.
    Document rõ ràng interface để implement sau.
    """

    def __init__(self, api_key: str = "", webhook_secret: str = ""):
        self._api_key = api_key or os.getenv("STRIPE_SECRET_KEY", "")
        self._webhook_secret = webhook_secret or os.getenv("STRIPE_WEBHOOK_SECRET", "")
        if not self._api_key:
            raise ValueError("STRIPE_SECRET_KEY is required for StripeProvider")

    def create_checkout_session(
        self,
        org_id: str,
        plan: PlanTier,
        success_url: str,
        cancel_url: str,
        user_id: str = "",
    ) -> CheckoutSession:
        """Tạo Stripe Checkout Session.

        Pseudocode:
            import stripe
            stripe.api_key = self._api_key
            session = stripe.checkout.Session.create(
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"org_id": org_id, "plan": plan.value, "user_id": user_id},
                line_items=[{"price": <stripe_price_id>, "quantity": 1}],
            )
            return CheckoutSession(
                id=session.id, url=session.url, provider="stripe", ...
            )
        """
        raise NotImplementedError(
            "StripeProvider.create_checkout_session requires `stripe` package and valid price IDs. "
            "Install stripe and implement the API call."
        )

    def create_portal_session(self, org_id: str) -> str:
        """Tạo Stripe Billing Portal session.

        Pseudocode:
            import stripe
            session = stripe.billing_portal.Session.create(
                customer=<stripe_customer_id>,
                return_url=<return_url>,
            )
            return session.url
        """
        raise NotImplementedError(
            "StripeProvider.create_portal_session requires `stripe` package and customer ID lookup."
        )

    def handle_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """Verify Stripe webhook signature và parse event.

        Pseudocode:
            import stripe
            event = stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
            return WebhookEvent(
                id=event.id, provider="stripe", event_type=event.type,
                payload=event.to_dict(), processed=False,
            )
        """
        raise NotImplementedError(
            "StripeProvider.handle_webhook requires `stripe` package. "
            "Use stripe.Webhook.construct_event for signature verification."
        )

    def get_subscription(self, provider_sub_id: str) -> dict:
        """Lấy Stripe subscription.

        Pseudocode:
            import stripe
            sub = stripe.Subscription.retrieve(provider_sub_id)
            return sub.to_dict()
        """
        raise NotImplementedError(
            "StripeProvider.get_subscription requires `stripe` package."
        )


# --- Factory ---
def get_billing_provider(provider: Optional[str] = None) -> AbstractPaymentProvider:
    """Factory tạo billing provider theo env BILLING_PROVIDER.

    Args:
        provider: mock | stripe | paddle. Mặc định đọc từ BILLING_PROVIDER env.

    Returns:
        AbstractPaymentProvider instance.
    """
    name = (provider or os.getenv("BILLING_PROVIDER", "mock")).lower()
    if name == "mock":
        return MockProvider()
    if name == "stripe":
        return StripeProvider()
    raise ValueError(f"Unsupported billing provider: {name}")
