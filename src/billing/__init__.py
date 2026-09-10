"""Billing package: provider-agnostic subscriptions & entitlements.

Works offline with a MockProvider so tests run without real Stripe/Paddle keys.
Swap in StripeProvider/PaddleProvider later without touching callers.
"""
from .models import PlanTier, SubscriptionStatus, Subscription, CheckoutSession, WebhookEvent
from .provider import AbstractPaymentProvider, MockProvider, StripeProvider, get_billing_provider
from .repository import BillingRepository
from .entitlements import check_entitlements, ENTITLEMENTS

__all__ = [
    "PlanTier",
    "SubscriptionStatus",
    "Subscription",
    "CheckoutSession",
    "WebhookEvent",
    "AbstractPaymentProvider",
    "MockProvider",
    "StripeProvider",
    "get_billing_provider",
    "BillingRepository",
    "check_entitlements",
    "ENTITLEMENTS",
]
