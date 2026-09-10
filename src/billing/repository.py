"""Repository cho Billing — lưu trữ subscriptions, checkout sessions,
webhook events, và entitlements trên SQLite.

Kế thừa từ SQLiteRepo, hỗ trợ :memory: cho tests.
"""
import json
import sqlite3
import threading
from datetime import datetime
from typing import Callable, List, Optional

from .models import (
    PlanTier, SubscriptionStatus, Subscription, CheckoutSession, WebhookEvent,
)


class BillingRepository:
    """Repository billing trên SQLite — hỗ trợ :memory:."""

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._mem_conn = None
        self._transaction_lock = threading.RLock()
        self._active_conn = threading.local()
        self._ensure_schema()

    def _connect(self):
        """Create a connection with bounded SQLite writer waiting."""
        active = getattr(self._active_conn, "conn", None)
        if active is not None:
            return active
        if self._db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(
                    ":memory:", timeout=10.0, check_same_thread=False
                )
                self._mem_conn.row_factory = sqlite3.Row
                self._configure_connection(self._mem_conn)
                self._ensure_schema_conn(self._mem_conn)
            return self._mem_conn
        conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._configure_connection(conn)
        self._ensure_schema_conn(conn)
        return conn

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA busy_timeout = 10000")

    def _ensure_schema(self):
        """Tạo schema nếu chưa tồn tại."""
        conn = self._connect()
        self._ensure_schema_conn(conn)

    def _ensure_schema_conn(self, conn: sqlite3.Connection):
        """Tạo bảng + index nếu chưa tồn tại."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                current_period_start TEXT,
                current_period_end TEXT,
                provider TEXT NOT NULL,
                provider_subscription_id TEXT,
                created_at TEXT NOT NULL,
                canceled_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkout_sessions (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                plan TEXT NOT NULL,
                provider TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entitlements (
                org_id TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                invoice_limit INTEGER NOT NULL DEFAULT 10,
                llm_access INTEGER NOT NULL DEFAULT 0,
                pdf_export INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_subscriptions_org_id
            ON subscriptions(org_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_events_processed
            ON webhook_events(processed)
        """)
        if self._db_path != ":memory:":
            conn.commit()

    def _commit(self, conn: sqlite3.Connection):
        """Commit and close file-backed connections."""
        if conn is getattr(self._active_conn, "conn", None):
            return
        if self._db_path != ":memory:":
            conn.commit()
            conn.close()
        else:
            conn.commit()

    def _connection_for_operation(self):
        """Use the webhook transaction connection for nested repository writes."""
        return getattr(self._active_conn, "conn", None) or self._connect()

    def process_webhook_idempotent(
        self,
        event: WebhookEvent,
        side_effect: Callable[[WebhookEvent], dict],
    ) -> tuple[bool, str, dict]:
        """Claim, apply, and complete a webhook in one SQLite transaction."""
        with self._transaction_lock:
            conn = self._connect()
            previous = getattr(self._active_conn, "conn", None)
            self._active_conn.conn = conn
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    """
                    INSERT INTO webhook_events
                        (id, provider, event_type, payload, processed, created_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (event.id, event.provider, event.event_type,
                     json.dumps(event.payload), event.created_at),
                )
                if cur.rowcount == 0:
                    row = conn.execute(
                        "SELECT processed FROM webhook_events WHERE id = ?", (event.id,)
                    ).fetchone()
                    conn.commit()
                    return False, "already_processed" if row and row["processed"] else "already_processing", {}

                result = side_effect(event)
                conn.execute("UPDATE webhook_events SET processed = 1 WHERE id = ?", (event.id,))
                conn.commit()
                return True, "processed", result or {}
            except Exception:
                conn.rollback()
                raise
            finally:
                if previous is None:
                    try:
                        del self._active_conn.conn
                    except AttributeError:
                        pass
                else:
                    self._active_conn.conn = previous
                if self._db_path != ":memory:":
                    conn.close()

    def create_subscription(self, sub: Subscription) -> Subscription:
        """Tạo subscription mới."""
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO subscriptions
            (id, org_id, user_id, plan, status, current_period_start, current_period_end,
             provider, provider_subscription_id, created_at, canceled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sub.id,
                sub.org_id,
                sub.user_id,
                sub.plan.value,
                sub.status.value,
                sub.current_period_start,
                sub.current_period_end,
                sub.provider,
                sub.provider_subscription_id,
                sub.created_at,
                sub.canceled_at,
            ),
        )
        self._commit(conn)
        return sub

    def get_subscription(self, sub_id: str) -> Optional[Subscription]:
        """Lấy subscription theo id nội bộ."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
        return self._row_to_subscription(row) if row else None

    def get_subscription_by_user(self, user_id: str) -> Optional[Subscription]:
        """Lấy subscription active gần nhất của user."""
        conn = self._connect()
        row = conn.execute(
            """
            SELECT * FROM subscriptions
            WHERE user_id = ? AND status != ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, SubscriptionStatus.CANCELED.value),
        ).fetchone()
        return self._row_to_subscription(row) if row else None

    def list_subscriptions(self, org_id: str) -> List[Subscription]:
        """Liệt kê subscriptions của một org."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE org_id = ?", (org_id,)
        ).fetchall()
        return [self._row_to_subscription(r) for r in rows]

    def update_subscription(self, sub_id: str, **kwargs) -> Optional[Subscription]:
        """Cập nhật subscription (partial update)."""
        if not kwargs:
            return self.get_subscription(sub_id)
        conn = self._connect()
        set_clauses = []
        values = []
        for key, value in kwargs.items():
            if key in (
                "plan", "status", "current_period_start", "current_period_end",
                "provider", "provider_subscription_id", "canceled_at",
            ):
                set_clauses.append(f"{key} = ?")
                values.append(value if not isinstance(value, (SubscriptionStatus, PlanTier)) else value.value)
        if not set_clauses:
            return self.get_subscription(sub_id)
        values.append(sub_id)
        conn.execute(f"UPDATE subscriptions SET {', '.join(set_clauses)} WHERE id = ?", values)
        self._commit(conn)
        return self.get_subscription(sub_id)

    def create_checkout_session(self, session: CheckoutSession) -> CheckoutSession:
        """Lưu checkout session."""
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO checkout_sessions
            (id, org_id, user_id, plan, provider, url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.org_id,
                session.user_id,
                session.plan.value,
                session.provider,
                session.url,
                session.status,
                session.created_at,
            ),
        )
        self._commit(conn)
        return session

    def get_checkout_session(self, session_id: str) -> Optional[CheckoutSession]:
        """Lấy checkout session theo id."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM checkout_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_checkout_session(row) if row else None

    def store_webhook_event(self, event: WebhookEvent) -> WebhookEvent:
        """Lưu webhook event."""
        conn = self._connect()
        conn.execute(
            """
            INSERT OR IGNORE INTO webhook_events (id, provider, event_type, payload, processed, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.provider,
                event.event_type,
                json.dumps(event.payload),
                1 if event.processed else 0,
                event.created_at,
            ),
        )
        self._commit(conn)
        return event

    def get_webhook_event(self, event_id: str) -> Optional[WebhookEvent]:
        """Lấy webhook event theo id."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM webhook_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        return WebhookEvent(
            id=row["id"],
            provider=row["provider"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            processed=bool(row["processed"]),
            created_at=row["created_at"],
        )

    def mark_webhook_processed(self, event_id: str) -> bool:
        """Đánh dấu webhook event đã xử lý."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE webhook_events SET processed = 1 WHERE id = ?", (event_id,)
        )
        self._commit(conn)
        return cur.rowcount > 0

    def get_entitlements(self, org_id: str) -> Optional[dict]:
        """Lấy entitlements của org."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM entitlements WHERE org_id = ?", (org_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "org_id": row["org_id"],
            "plan": row["plan"],
            "invoice_limit": row["invoice_limit"],
            "llm_access": bool(row["llm_access"]),
            "pdf_export": bool(row["pdf_export"]),
        }

    def upsert_entitlements(
        self,
        org_id: str,
        plan: PlanTier,
        invoice_limit: int = 10,
        llm_access: bool = False,
        pdf_export: bool = False,
    ) -> dict:
        """Upsert entitlements cho org."""
        now = datetime.utcnow().isoformat()
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO entitlements (org_id, plan, invoice_limit, llm_access, pdf_export, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(org_id) DO UPDATE SET
                plan = excluded.plan,
                invoice_limit = excluded.invoice_limit,
                llm_access = excluded.llm_access,
                pdf_export = excluded.pdf_export,
                updated_at = excluded.updated_at
            """,
            (org_id, plan.value, invoice_limit, 1 if llm_access else 0, 1 if pdf_export else 0, now),
        )
        self._commit(conn)
        return {
            "org_id": org_id,
            "plan": plan.value,
            "invoice_limit": invoice_limit,
            "llm_access": llm_access,
            "pdf_export": pdf_export,
        }

    def _row_to_subscription(self, row: sqlite3.Row) -> Subscription:
        """Chuyển row DB thành Subscription model."""
        return Subscription(
            id=row["id"],
            org_id=row["org_id"],
            user_id=row["user_id"],
            plan=PlanTier(row["plan"]) if row["plan"] in (p.value for p in PlanTier) else PlanTier.FREE,
            status=SubscriptionStatus(row["status"]) if row["status"] in (s.value for s in SubscriptionStatus) else SubscriptionStatus.ACTIVE,
            current_period_start=row["current_period_start"],
            current_period_end=row["current_period_end"],
            provider=row["provider"],
            provider_subscription_id=row["provider_subscription_id"],
            created_at=row["created_at"],
            canceled_at=row["canceled_at"],
        )

    def _row_to_checkout_session(self, row: sqlite3.Row) -> CheckoutSession:
        """Chuyển row DB thành CheckoutSession model."""
        return CheckoutSession(
            id=row["id"],
            org_id=row["org_id"],
            user_id=row["user_id"],
            plan=PlanTier(row["plan"]) if row["plan"] in (p.value for p in PlanTier) else PlanTier.FREE,
            provider=row["provider"],
            url=row["url"],
            status=row["status"],
            created_at=row["created_at"],
        )
