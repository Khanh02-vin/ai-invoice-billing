"""Lưu trữ Subscription Billing trên SQLite.

Schema:
- plans: danh sách gói (free/pro/enterprise).
- subscriptions: map từ provider subscription.
- entitlements: quyền lợi hiện tại (derived từ active sub).
- webhook_events: raw event để idempotency & audit.
"""
import json
import sqlite3
from datetime import datetime
from typing import List, Optional

from ..domain.billing import (
    Plan, PlanTier, Subscription, SubscriptionStatus,
    Entitlement, CheckoutSession, WebhookEvent,
)
from .db import SQLiteRepo


class BillingRepository(SQLiteRepo):
    """Repository billing trên SQLite."""

    def _ensure_schema(self, conn: sqlite3.Connection):
        """Tạo bảng + index nếu chưa tồn tại."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                tier TEXT,
                name TEXT,
                price_cents INTEGER,
                currency TEXT,
                interval TEXT,
                features TEXT,
                invoice_limit INTEGER,
                llm_enabled INTEGER,
                pdf_export INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                org_id TEXT,
                plan_id TEXT,
                status TEXT,
                current_period_start TEXT,
                current_period_end TEXT,
                provider TEXT,
                provider_subscription_id TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entitlements (
                user_id TEXT,
                org_id TEXT,
                plan_tier TEXT,
                invoice_limit INTEGER,
                llm_enabled INTEGER,
                pdf_export INTEGER,
                updated_at TEXT,
                UNIQUE(user_id, org_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id TEXT PRIMARY KEY,
                provider TEXT,
                event_type TEXT,
                payload TEXT,
                processed INTEGER,
                created_at TEXT
            )
        """)
        # Index
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_org ON subscriptions(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_provider ON subscriptions(provider_subscription_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ent_user ON entitlements(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ent_org ON entitlements(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_idem ON webhook_events(id)")

    # ---------- Plans ----------
    def seed_default_plans(self) -> List[Plan]:
        """Seed 3 gói mặc định nếu chưa có."""
        existing = self.list_plans()
        if existing:
            return existing
        plans = [
            Plan(
                id="plan_free",
                tier=PlanTier.FREE,
                name="Free",
                price_cents=0,
                currency="USD",
                interval="month",
                features={"support": "community"},
                invoice_limit=10,
                llm_enabled=False,
                pdf_export=False,
            ),
            Plan(
                id="plan_pro",
                tier=PlanTier.PRO,
                name="Pro",
                price_cents=1900,
                currency="USD",
                interval="month",
                features={"support": "email", "priority": True},
                invoice_limit=100,
                llm_enabled=True,
                pdf_export=True,
            ),
            Plan(
                id="plan_enterprise",
                tier=PlanTier.ENTERPRISE,
                name="Enterprise",
                price_cents=9900,
                currency="USD",
                interval="month",
                features={"support": "dedicated", "sla": "99.9%"},
                invoice_limit=10000,
                llm_enabled=True,
                pdf_export=True,
            ),
        ]
        with self._connect() as conn:
            for p in plans:
                conn.execute(
                    "INSERT OR IGNORE INTO plans (id, tier, name, price_cents, currency, interval, features, invoice_limit, llm_enabled, pdf_export) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (p.id, p.tier.value, p.name, p.price_cents, p.currency, p.interval,
                     json.dumps(p.features), p.invoice_limit, int(p.llm_enabled), int(p.pdf_export)),
                )
        return plans

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Lấy plan theo id."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return self._row_to_plan(row) if row else None

    def list_plans(self) -> List[Plan]:
        """Liệt kê tất cả plans."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM plans ORDER BY price_cents").fetchall()
        return [self._row_to_plan(r) for r in rows]

    # ---------- Subscriptions ----------
    def create_subscription(self, sub: Subscription) -> Subscription:
        """Tạo subscription mới."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO subscriptions (id, user_id, org_id, plan_id, status, current_period_start, current_period_end, provider, provider_subscription_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sub.id, sub.user_id, sub.org_id, sub.plan_id, sub.status.value,
                 sub.current_period_start, sub.current_period_end, sub.provider,
                 sub.provider_subscription_id, sub.created_at),
            )
        return sub

    def get_subscription(self, sub_id: str) -> Optional[Subscription]:
        """Lấy subscription theo id."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
        return self._row_to_sub(row) if row else None

    def get_subscription_by_user(self, user_id: str) -> Optional[Subscription]:
        """Lấy subscription active gần nhất của user."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return self._row_to_sub(row) if row else None

    def update_subscription_status(self, sub_id: str, status: SubscriptionStatus) -> None:
        """Cập nhật status subscription."""
        with self._connect() as conn:
            conn.execute("UPDATE subscriptions SET status = ? WHERE id = ?", (status.value, sub_id))

    # ---------- Entitlements ----------
    def get_entitlements(self, user_id: str, org_id: str = "") -> Optional[Entitlement]:
        """Lấy entitlement — ưu tiến org-level nếu có."""
        with self._connect() as conn:
            if org_id:
                row = conn.execute(
                    "SELECT * FROM entitlements WHERE org_id = ? LIMIT 1", (org_id,)
                ).fetchone()
                if row:
                    return self._row_to_ent(row)
            row = conn.execute(
                "SELECT * FROM entitlements WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
        return self._row_to_ent(row) if row else None

    def upsert_entitlements(self, ent: Entitlement) -> Entitlement:
        """Upsert entitlement (theo user_id + org_id)."""
        ent.updated_at = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO entitlements (user_id, org_id, plan_tier, invoice_limit, llm_enabled, pdf_export, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, org_id) DO UPDATE SET
                       plan_tier = excluded.plan_tier,
                       invoice_limit = excluded.invoice_limit,
                       llm_enabled = excluded.llm_enabled,
                       pdf_export = excluded.pdf_export,
                       updated_at = excluded.updated_at""",
                (ent.user_id, ent.org_id, ent.plan_tier.value, ent.invoice_limit,
                 int(ent.llm_enabled), int(ent.pdf_export), ent.updated_at),
            )
        return ent

    # ---------- Webhook events ----------
    def store_webhook_event(self, event: WebhookEvent) -> WebhookEvent:
        """Lưu raw webhook event."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO webhook_events (id, provider, event_type, payload, processed, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (event.id, event.provider, event.event_type, json.dumps(event.payload),
                 int(event.processed), event.created_at),
            )
        return event

    def mark_webhook_processed(self, event_id: str) -> None:
        """Đánh dấu event đã xử lý."""
        with self._connect() as conn:
            conn.execute("UPDATE webhook_events SET processed = 1 WHERE id = ?", (event_id,))

    def get_webhook_event(self, event_id: str) -> Optional[WebhookEvent]:
        """Lấy webhook event theo id (idempotency check)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM webhook_events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_wh(row) if row else None

    # ---------- Row mappers ----------
    @staticmethod
    def _row_to_plan(row) -> Plan:
        return Plan(
            id=row["id"],
            tier=PlanTier(row["tier"]),
            name=row["name"],
            price_cents=row["price_cents"],
            currency=row["currency"],
            interval=row["interval"],
            features=json.loads(row["features"]) if row["features"] else {},
            invoice_limit=row["invoice_limit"],
            llm_enabled=bool(row["llm_enabled"]),
            pdf_export=bool(row["pdf_export"]),
        )

    @staticmethod
    def _row_to_sub(row) -> Subscription:
        return Subscription(
            id=row["id"],
            user_id=row["user_id"],
            org_id=row["org_id"] or "",
            plan_id=row["plan_id"],
            status=SubscriptionStatus(row["status"]),
            current_period_start=row["current_period_start"],
            current_period_end=row["current_period_end"],
            provider=row["provider"],
            provider_subscription_id=row["provider_subscription_id"] or "",
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_ent(row) -> Entitlement:
        return Entitlement(
            user_id=row["user_id"],
            org_id=row["org_id"] or "",
            plan_tier=PlanTier(row["plan_tier"]),
            invoice_limit=row["invoice_limit"],
            llm_enabled=bool(row["llm_enabled"]),
            pdf_export=bool(row["pdf_export"]),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_wh(row) -> WebhookEvent:
        return WebhookEvent(
            id=row["id"],
            provider=row["provider"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            processed=bool(row["processed"]),
            created_at=row["created_at"],
        )
