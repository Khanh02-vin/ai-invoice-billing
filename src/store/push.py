"""Lưu trữ Web Push subscription (PWA thông báo native) trên SQLite.

Mỗi dòng = 1 thiết bị (endpoint) của 1 user. Endpoint do trình duyệt cấp,
là khoá tự nhiên của thiết bị; subscribe lại cùng endpoint chỉ cập nhật key.
"""
import uuid
from datetime import datetime
from typing import Dict, List

from .db import SQLiteRepo


class PushSubscriptionRepository(SQLiteRepo):
    """Repository subscription — schema tự tạo khi lần đầu kết nối."""

    def _ensure_schema(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT
            )
        """)
        # Mỗi thiết bị chỉ giữ 1 dòng cho mỗi user (subscribe lại = update key)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_push_user_endpoint
            ON push_subscriptions (user_id, endpoint)
        """)

    def save(self, user_id: str, subscription: Dict) -> None:
        """Lưu/cập nhật subscription. Chuẩn dict: {endpoint, keys:{p256dh, auth}}."""
        endpoint = (subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys") or {}
        p256dh = (keys.get("p256dh") or "").strip()
        auth = (keys.get("auth") or "").strip()
        if not (user_id and endpoint and p256dh and auth):
            raise ValueError("subscription thiếu endpoint/keys")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO push_subscriptions (id, user_id, endpoint, p256dh, auth, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, endpoint)
                   DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth""",
                (uuid.uuid4().hex[:16], user_id, endpoint, p256dh, auth,
                 datetime.utcnow().isoformat()),
            )

    def list_for_user(self, user_id: str) -> List[Dict]:
        """Subscription của user, dạng pywebpush nhận trực tiếp."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [
            {"endpoint": r["endpoint"], "keys": {"p256dh": r["p256dh"], "auth": r["auth"]}}
            for r in rows
        ]

    def delete(self, user_id: str, endpoint: str = "") -> int:
        """Xoá 1 endpoint (unsubscribe) hoặc toàn bộ thiết bị của user."""
        with self._connect() as conn:
            if endpoint:
                cur = conn.execute(
                    "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
                    (user_id, endpoint),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,)
                )
            return cur.rowcount
