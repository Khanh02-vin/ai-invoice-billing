"""Mô hình miền cho Subscription Billing (Stripe/Paddle adapter).

Nguyên tắc:
- Provider-agnostic: Plan/Subscription/Entitlement không phụ thuộc Stripe/Paddle.
- Schema đầy đủ để map trực tiếp từ webhook events (Stripe/Paddle).
- Self-contained trong src/billing/ — không phụ thuộc src/domain/billing.py.
"""
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# --- Enums ---
class PlanTier(str, Enum):
    """Gói đăng ký — provider-agnostic."""
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    """Trạng thái subscription — map từ Stripe/Paddle."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"


# --- Domain models ---
class Subscription(BaseModel):
    """Subscription map từ Stripe `customer.subscription.*` / Paddle `subscription.*`.

    Fields:
        id: UUID nội bộ.
        org_id: tổ chức sở hữu (multi-tenant).
        user_id: người tạo / sở hữu.
        plan: PlanTier (free/pro/enterprise).
        status: active/past_due/canceled/trialing.
        current_period_start/end: ISO datetime của kỳ hiện tại.
        provider: mock | stripe | paddle.
        provider_subscription_id: ID subscription ở provider.
        created_at: ISO datetime tạo.
        canceled_at: ISO datetime hủy (nếu có).
    """
    id: str = ""
    org_id: str = ""
    user_id: str = ""
    plan: PlanTier = PlanTier.FREE
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    provider: str = "mock"
    provider_subscription_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    canceled_at: Optional[str] = None


class CheckoutSession(BaseModel):
    """Checkout session — map từ Stripe Checkout / Paddle Checkout."""
    id: str = ""
    org_id: str = ""
    user_id: str = ""
    plan: PlanTier = PlanTier.FREE
    provider: str = "mock"
    url: str = ""
    status: str = "open"  # open | complete | expired
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class WebhookEvent(BaseModel):
    """Raw webhook event — dùng cho idempotency & audit."""
    id: str = ""
    provider: str = "mock"
    event_type: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    processed: bool = False
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
