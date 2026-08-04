"""Lưu trữ hóa đơn trên SQLite."""
import sqlite3
from datetime import datetime
from typing import List, Optional
from ..domain.models import Invoice, InvoiceStatus, InvoiceUpdate, MonthlyReport
from .db import SQLiteRepo


class InvoiceRepository(SQLiteRepo):
    """Repository hóa đơn trên SQLite."""

    def _ensure_schema(self, conn):
        """Tạo bảng và index nếu chưa tồn tại. Migration cho DB cũ."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                invoice_number TEXT,
                vendor TEXT,
                issue_date TEXT,
                due_date TEXT,
                currency TEXT,
                total REAL,
                tax REAL,
                discount REAL DEFAULT 0,
                status TEXT,
                source_file TEXT,
                raw_snippet TEXT,
                confidence REAL,
                user_id TEXT DEFAULT '',
                created_at TEXT
            )
        """)
        # Migration: DB tạo trước khi có cột mới
        cols = [r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()]
        if "user_id" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN user_id TEXT DEFAULT ''")
        if "discount" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN discount REAL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(issue_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_user ON invoices(user_id)")

    def upsert(self, invoice: Invoice) -> Invoice:
        """Lưu hóa đơn (thêm mới hoặc cập nhật theo id)."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO invoices (id, invoice_number, vendor, issue_date, due_date,
                   currency, total, tax, discount, status, source_file, raw_snippet, confidence, user_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     invoice_number=excluded.invoice_number,
                     vendor=excluded.vendor,
                     total=excluded.total,
                     tax=excluded.tax,
                     discount=excluded.discount,
                     status=excluded.status""",
                (invoice.id, invoice.invoice_number, invoice.vendor, invoice.issue_date,
                 invoice.due_date, invoice.currency, invoice.total, invoice.tax,
                 invoice.discount, invoice.status.value, invoice.source_file, invoice.raw_snippet,
                 invoice.confidence, invoice.user_id, invoice.created_at.isoformat()),
            )
        return invoice

    def get(self, invoice_id: str, user_id: str = "") -> Optional[Invoice]:
        """Lấy hóa đơn theo id (phải thuộc user)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE id = ? AND user_id = ?", (invoice_id, user_id)
            ).fetchone()
        return self._row_to_invoice(row) if row else None

    def list(self, user_id: str = "", status: Optional[InvoiceStatus] = None, limit: int = 100) -> List[Invoice]:
        """Liệt kê hóa đơn của user, có thể lọc theo status."""
        query = "SELECT * FROM invoices WHERE user_id = ?"
        params = (user_id,)
        if status:
            query += " AND status = ?"
            params += (status.value,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_invoice(r) for r in rows]

    def update(self, invoice_id: str, changes: InvoiceUpdate, user_id: str = "") -> Optional[Invoice]:
        """Cập nhật một số trường hóa đơn (phải thuộc user)."""
        fields = changes.model_dump(exclude_none=True)
        if not fields:
            return self.get(invoice_id, user_id)
        if "status" in fields:
            fields["status"] = fields["status"].value
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE invoices SET {assignments} WHERE id = ? AND user_id = ?",
                (*fields.values(), invoice_id, user_id),
            )
        if cur.rowcount == 0:
            return None
        return self.get(invoice_id, user_id)

    def delete(self, invoice_id: str, user_id: str = "") -> bool:
        """Xóa hóa đơn (phải thuộc user). Trả về True nếu có."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM invoices WHERE id = ? AND user_id = ?", (invoice_id, user_id)
            )
        return cur.rowcount > 0

    def monthly_report(self, period: str, user_id: str = "") -> Optional[MonthlyReport]:
        """Thống kê theo tháng (YYYY-MM) của user. Lọc theo issue_date."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoices WHERE substr(issue_date, 1, 7) = ? AND user_id = ?",
                (period, user_id),
            ).fetchall()
        if not rows:
            return None
        invoices = [self._row_to_invoice(r) for r in rows]
        paid = [i for i in invoices if i.status == InvoiceStatus.PAID]
        unpaid = [i for i in invoices if i.status in (InvoiceStatus.UNPAID, InvoiceStatus.OVERDUE)]
        return MonthlyReport(
            period=period,
            invoice_count=len(invoices),
            total_amount=sum(i.total for i in invoices),
            total_tax=sum(i.tax for i in invoices),
            total_discount=sum(i.discount for i in invoices),
            paid_amount=sum(i.total for i in paid),
            unpaid_amount=sum(i.total for i in unpaid),
            paid_count=len(paid),
            unpaid_count=len(unpaid),
        )

    def _row_to_invoice(self, row: sqlite3.Row) -> Invoice:
        """Chuyển dòng SQLite thành Invoice."""
        return Invoice(
            id=row["id"],
            invoice_number=row["invoice_number"],
            vendor=row["vendor"],
            issue_date=row["issue_date"],
            due_date=row["due_date"],
            currency=row["currency"],
            total=row["total"],
            tax=row["tax"],
            status=InvoiceStatus(row["status"]),
            source_file=row["source_file"],
            raw_snippet=row["raw_snippet"],
            confidence=row["confidence"],
            user_id=row["user_id"],
            discount=row["discount"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )