"""Lưu trữ giao dịch thanh toán (bank/wallet) trên SQLite — chờ ghép hóa đơn."""
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
                created_at TEXT
            )
        """)
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
            tx.status.value, tx.matched_invoice_id, tx.raw_text,
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
                    source, external_ref, status, matched_invoice_id, raw_text, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._serialize(tx),
            )
        return tx

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
        query += " ORDER BY created_at DESC LIMIT ?"
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
