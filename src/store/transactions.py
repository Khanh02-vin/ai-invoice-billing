"""Lưu trữ giao dịch thanh toán (bank/wallet) trên SQLite — chờ ghép hóa đơn."""
import math
import uuid
from datetime import datetime
from typing import List, Optional

from ..domain.payments import PaymentTransaction, TxDirection, TxStatus
from .db import SQLiteRepo


class TransactionRepository(SQLiteRepo):
    """Repository giao dịch — schema tự tạo khi lần đầu kết nối."""

    def ping(self) -> bool:
        with self._connect() as conn:
            conn.execute("SELECT 1")
        return True

    def _ensure_schema(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'VND',
                direction TEXT NOT NULL DEFAULT 'debit',
                merchant TEXT DEFAULT '',
                occurred_at TEXT,
                source TEXT NOT NULL DEFAULT 'sms',
                external_ref TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending_receipt',
                matched_invoice_id TEXT DEFAULT '',
                raw_text TEXT DEFAULT '',
                note TEXT DEFAULT '',
                category TEXT DEFAULT '',
                location_lat REAL,
                location_lon REAL,
                location_captured_at TEXT,
                created_at TEXT
            )
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(payment_transactions)")}
        for name, sql_type in (("note", "TEXT DEFAULT ''"), ("category", "TEXT DEFAULT ''"),
                               ("location_lat", "REAL"), ("location_lon", "REAL"),
                               ("location_captured_at", "TEXT")):
            if name not in columns:
                conn.execute(f"ALTER TABLE payment_transactions ADD COLUMN {name} {sql_type}")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transaction_attachments (
                id TEXT PRIMARY KEY,
                transaction_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                storage_key TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_attachment_owner ON transaction_attachments(user_id, transaction_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shared_place_visits (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                place_name TEXT NOT NULL,
                place_kind TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shared_place_user ON shared_place_visits(user_id, created_at)")
        # Dedupe: mỗi user không có 2 transaction cùng ref
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_user_ref
            ON payment_transactions (user_id, external_ref)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tx_user_status
            ON payment_transactions (user_id, status)
        """)

    def _serialize(self, tx: PaymentTransaction) -> tuple:
        return (
            tx.id, tx.user_id, tx.amount, tx.currency, tx.direction.value,
            tx.merchant, tx.occurred_at, tx.source, tx.external_ref,
            tx.status.value, tx.matched_invoice_id, tx.raw_text, tx.note, tx.category,
            tx.location_lat, tx.location_lon, tx.location_captured_at,
            tx.created_at.isoformat(),
        )

    def _row_to_tx(self, row) -> PaymentTransaction:
        data = dict(row)
        data["direction"] = TxDirection(data.get("direction", "debit"))
        data["status"] = TxStatus(data.get("status", "pending_receipt"))
        data["created_at"] = (
            datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow()
        )
        return PaymentTransaction(**data)

    def create(self, tx: PaymentTransaction) -> PaymentTransaction:
        """Lưu giao dịch mới. External_ref trùng của cùng user -> return bản đã có."""
        if not tx.id:
            tx.id = uuid.uuid4().hex[:16]
        existing = self.get_by_ref(tx.user_id, tx.external_ref)
        if existing:
            return existing
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO payment_transactions
                   (id, user_id, amount, currency, direction, merchant, occurred_at,
                    source, external_ref, status, matched_invoice_id, raw_text, note, category,
                    location_lat, location_lon, location_captured_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._serialize(tx),
            )
        return tx

    def add_attachment(self, attachment_id: str, transaction_id: str, user_id: str,
                       storage_key: str, mime_type: str, size: int) -> dict:
        created_at = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO transaction_attachments
                   (id, transaction_id, user_id, storage_key, mime_type, size, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (attachment_id, transaction_id, user_id, storage_key, mime_type, size, created_at),
            )
        return {"id": attachment_id, "transaction_id": transaction_id, "mime_type": mime_type,
                "size": size, "created_at": created_at}

    def list_attachments(self, transaction_id: str, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, transaction_id, mime_type, size, created_at
                   FROM transaction_attachments WHERE transaction_id = ? AND user_id = ?
                   ORDER BY created_at DESC""", (transaction_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_attachment(self, attachment_id: str, user_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM transaction_attachments WHERE id = ? AND user_id = ?",
                (attachment_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_attachment(self, attachment_id: str, user_id: str) -> Optional[dict]:
        attachment = self.get_attachment(attachment_id, user_id)
        if not attachment:
            return None
        with self._connect() as conn:
            conn.execute("DELETE FROM transaction_attachments WHERE id = ? AND user_id = ?", (attachment_id, user_id))
        return attachment

    def add_shared_place_visit(self, user_id: str, place_name: str, place_kind: str,
                               lat: float, lon: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO shared_place_visits (id, user_id, place_name, place_kind, lat, lon, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, user_id, place_name, place_kind, lat, lon, datetime.utcnow().isoformat()),
            )

    def list_shared_places(self, lat: float, lon: float, radius: int, user_ids: list[str], exclude_user_id: str = "") -> list[dict]:
        user_ids = list(set(user_ids) - ({exclude_user_id} if exclude_user_id else set()))
        if len(user_ids) < 2:
            return []
        lat_delta = radius / 111_000
        lon_delta = radius / max(1, 111_000 * abs(math.cos(math.radians(lat))))
        marks = ",".join("?" for _ in user_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT place_name AS name, place_kind AS kind,
                           ROUND(AVG(lat), 2) AS lat, ROUND(AVG(lon), 2) AS lon,
                           COUNT(DISTINCT user_id) AS visitors
                    FROM shared_place_visits
                    WHERE user_id IN ({marks}) AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                    GROUP BY place_name, place_kind, ROUND(lat, 2), ROUND(lon, 2)
                    HAVING COUNT(DISTINCT user_id) >= 2 ORDER BY visitors DESC LIMIT 20""",
                (*user_ids, lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_details(self, tx_id: str, user_id: str, *, note: str, category: str,
                       location_lat: Optional[float], location_lon: Optional[float],
                       location_captured_at: Optional[str]) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE payment_transactions
                   SET note = ?, category = ?, location_lat = ?, location_lon = ?, location_captured_at = ?
                   WHERE id = ? AND user_id = ?""",
                (note, category, location_lat, location_lon, location_captured_at, tx_id, user_id),
            )
            return cur.rowcount > 0

    def get(self, tx_id: str, user_id: str = "") -> Optional[PaymentTransaction]:
        query = "SELECT * FROM payment_transactions WHERE id = ?"
        params: list = [tx_id]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_to_tx(row) if row else None

    def get_by_ref(self, user_id: str, external_ref: str) -> Optional[PaymentTransaction]:
        if not external_ref:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_transactions WHERE user_id = ? AND external_ref = ?",
                (user_id, external_ref),
            ).fetchone()
        return self._row_to_tx(row) if row else None

    def list(
        self,
        user_id: str = "",
        status: Optional[TxStatus] = None,
        direction: Optional[TxDirection] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[PaymentTransaction]:
        query = "SELECT * FROM payment_transactions WHERE user_id = ?"
        params: list = [user_id]
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if direction:
            query += " AND direction = ?"
            params.append(direction.value)
        if start_date:
            query += " AND occurred_at >= ?"
            params.append(start_date.isoformat())
        if end_date:
            query += " AND occurred_at < ?"
            params.append(end_date.isoformat())
        query += " ORDER BY occurred_at DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_tx(r) for r in rows]

    def set_status(
        self, tx_id: str, status: TxStatus, invoice_id: str = "", user_id: str = ""
    ) -> bool:
        query = "UPDATE payment_transactions SET status = ?, matched_invoice_id = ? WHERE id = ?"
        params: list = [status.value, invoice_id, tx_id]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        with self._connect() as conn:
            cur = conn.execute(query, params)
            return cur.rowcount > 0
