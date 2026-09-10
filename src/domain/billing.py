"""Mô hình miền cho Subscription Billing (Stripe/Paddle adapter).

Nguyên tắc:
- Provider-agnostic: Plan/Subscription/Entitlement không phụ thuộc Stripe/Paddle.
- Schema đầy đủ để map trực tiếp từ webhook events (Stripe/Paddle).
"""
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# --- Enums ---
class PlanTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"


# --- Domain models ---
class Plan(BaseModel):
    """Gói đăng ký (plan/product) — provider-agnostic."""
    id: str = ""
    tier: PlanTier = PlanTier.FREE
    name: str = ""
    price_cents: int = 0
    currency: str = "USD"
    interval: str = "month"  # month | year
    features: Dict[str, Any] = Field(default_factory=dict)
    invoice_limit: int = 10  # số hóa đơn tối đa / kỳ
    llm_enabled: bool = False
    pdf_export: bool = False


class Subscription(BaseModel):
    """Subscription map từ Stripe `customer.subscription.*` / Paddle `subscription.*`."""
    id: str = ""
    user_id: str = ""
    org_id: str = ""
    plan_id: str = ""
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    provider: str = "mock"  # mock | stripe | paddle
    provider_subscription_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Entitlement(BaseModel):
    """Quyền lợi hiện tại của user/org — derived từ active subscription."""
    user_id: str = ""
    org_id: str = ""
    plan_tier: PlanTier = PlanTier.FREE
    invoice_limit: int = 10
    llm_enabled: bool = False
    pdf_export: bool = False
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class CheckoutSession(BaseModel):
    """Phiên checkout — trả từ provider.create_checkout_session."""
    id: str = ""
    user_id: str = ""
    plan_id: str = ""
    url: str = ""
    status: str = "open"  # open | complete | expired
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class WebhookEvent(BaseModel):
    """Webhook event raw — lưu lại để audit & idempotency."""
    id: str = ""
    provider: str = "mock"
    event_type: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    processed: bool = False
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
