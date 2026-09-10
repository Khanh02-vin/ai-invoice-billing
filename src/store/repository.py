"""Lưu trữ hóa đơn trên SQLite.

Schema versioned: hỗ trợ line items, tax IDs, provenance, job status.
Migration cho DB cũ: tự thêm cột mới nếu thiếu.
"""
import json
import sqlite3
from datetime import datetime
from typing import List, Optional
from ..domain.models import Invoice, InvoiceStatus, InvoiceUpdate, MonthlyReport, JobStatus, LineItem
from .db import SQLiteRepo


class InvoiceRepository(SQLiteRepo):
    """Repository hóa đơn trên SQLite."""

    def ping(self) -> bool:
        """Kiểm tra DB connection sẵn sàng."""
        with self._connect() as conn:
            conn.execute("SELECT 1")
        return True

    def _ensure_schema(self, conn):
        """Tạo bảng và index nếu chưa tồn tại. Migration cho DB cũ."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                invoice_number TEXT,
                vendor TEXT,
                buyer TEXT DEFAULT '',
                issue_date TEXT,
                due_date TEXT,
                currency TEXT,
                subtotal REAL DEFAULT 0,
                total REAL,
                tax REAL,
                tax_rate REAL DEFAULT 0,
                discount REAL DEFAULT 0,
                amount_due REAL DEFAULT 0,
                status TEXT,
                source_file TEXT,
                raw_snippet TEXT,
                confidence REAL,
                user_id TEXT DEFAULT '',
                created_at TEXT,
                schema_version INTEGER DEFAULT 1,
                supplier_tax_id TEXT DEFAULT '',
                customer_tax_id TEXT DEFAULT '',
                line_items TEXT DEFAULT '[]',
                provenance TEXT DEFAULT '{}',
                job_status TEXT DEFAULT 'review',
                finalized_at TEXT,
                finalized_by TEXT,
                file_checksum TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                page_count INTEGER,
                extraction_provider TEXT DEFAULT '',
                review_history TEXT DEFAULT '[]'
            )
        """)
        # Migration: DB tạo trước khi có cột mới
        cols = [r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()]
        migrations = [
            ("buyer", "TEXT DEFAULT ''"),
            ("subtotal", "REAL DEFAULT 0"),
            ("tax_rate", "REAL DEFAULT 0"),
            ("amount_due", "REAL DEFAULT 0"),
            ("schema_version", "INTEGER DEFAULT 1"),
            ("supplier_tax_id", "TEXT DEFAULT ''"),
            ("customer_tax_id", "TEXT DEFAULT ''"),
            ("line_items", "TEXT DEFAULT '[]'"),
            ("provenance", "TEXT DEFAULT '{}'"),
            ("job_status", "TEXT DEFAULT 'review'"),
            ("finalized_at", "TEXT"),
            ("finalized_by", "TEXT"),
            ("file_checksum", "TEXT DEFAULT ''"),
            ("file_size", "INTEGER DEFAULT 0"),
            ("page_count", "INTEGER"),
            ("extraction_provider", "TEXT DEFAULT ''"),
            ("review_history", "TEXT DEFAULT '[]'"),
        ]
        for col, typ in migrations:
            if col not in cols:
                conn.execute(f"ALTER TABLE invoices ADD COLUMN {col} {typ}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(issue_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_user ON invoices(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_job_status ON invoices(job_status)")

    def _serialize_invoice(self, invoice: Invoice) -> tuple:
        """Serialize Invoice model -> tuple cho DB."""
        return (
            invoice.id,
            invoice.invoice_number,
            invoice.vendor,
            invoice.buyer,
            invoice.issue_date,
            invoice.due_date,
            invoice.currency,
            invoice.subtotal,
            invoice.total,
            invoice.tax,
            invoice.tax_rate,
            invoice.discount,
            invoice.amount_due,
            invoice.status.value,
            invoice.source_file,
            invoice.raw_snippet,
            invoice.confidence,
            invoice.user_id,
            invoice.created_at.isoformat(),
            invoice.schema_version,
            invoice.supplier_tax_id,
            invoice.customer_tax_id,
            json.dumps([item.model_dump() for item in invoice.line_items]),
            json.dumps({k: v.model_dump() for k, v in invoice.provenance.items()}),
            invoice.job_status.value,
            invoice.finalized_at.isoformat() if invoice.finalized_at else None,
            invoice.finalized_by,
            invoice.file_checksum,
            invoice.file_size,
            invoice.page_count,
            invoice.extraction_provider,
            json.dumps(invoice.review_history),
        )

    def _row_to_invoice(self, row) -> Invoice:
        """Chuyển DB row -> Invoice model."""
        data = dict(row)
        # Parse JSON fields
        data["line_items"] = [LineItem(**item) for item in json.loads(data.get("line_items") or "[]")]
        prov_raw = json.loads(data.get("provenance") or "{}")
        data["provenance"] = {k: FieldProvenance(**v) for k, v in prov_raw.items()}
        data["review_history"] = json.loads(data.get("review_history") or "[]")
        # Parse enums
        data["status"] = InvoiceStatus(data.get("status", "unpaid"))
        data["job_status"] = JobStatus(data.get("job_status", "review"))
        # Parse datetime
        data["created_at"] = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow()
        if data.get("finalized_at"):
            data["finalized_at"] = datetime.fromisoformat(data["finalized_at"])
        return Invoice(**data)

    def create(self, data, user_id: str = "") -> Invoice:
        """Tạo hóa đơn mới từ InvoiceCreate."""
        import uuid
        invoice = Invoice(
            id=str(uuid.uuid4()),
            invoice_number=data.invoice_number,
            vendor=data.vendor,
            issue_date=data.issue_date,
            due_date=data.due_date,
            currency=data.currency,
            total=data.total,
            tax=data.tax,
            status=data.status,
            user_id=user_id,
        )
        return self.upsert(invoice)

    def upsert(self, invoice: Invoice) -> Invoice:
        """Lưu hóa đơn (thêm mới hoặc cập nhật theo id)."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO invoices (id, invoice_number, vendor, buyer, issue_date, due_date,
                   currency, subtotal, total, tax, tax_rate, discount, amount_due, status,
                   source_file, raw_snippet, confidence, user_id, created_at,
                   schema_version, supplier_tax_id, customer_tax_id, line_items, provenance,
                   job_status, finalized_at, finalized_by, file_checksum, file_size, page_count,
                   extraction_provider, review_history)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     invoice_number=excluded.invoice_number,
                     vendor=excluded.vendor,
                     buyer=excluded.buyer,
                     total=excluded.total,
                     tax=excluded.tax,
                     discount=excluded.discount,
                     status=excluded.status,
                     job_status=excluded.job_status,
                     line_items=excluded.line_items,
                     provenance=excluded.provenance,
                     review_history=excluded.review_history""",
                self._serialize_invoice(invoice),
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
        params: list = [user_id]
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_invoice(r) for r in rows]

    def update(self, invoice_id: str, data: InvoiceUpdate, user_id: str = "") -> Optional[Invoice]:
        """Cập nhật hóa đơn."""
        invoice = self.get(invoice_id, user_id=user_id)
        if not invoice:
            return None
        if data.status is not None:
            invoice.status = data.status
        if data.total is not None:
            invoice.total = data.total
        if data.vendor is not None:
            invoice.vendor = data.vendor
        return self.upsert(invoice)

    def delete(self, invoice_id: str, user_id: str = "") -> bool:
        """Xóa hóa đơn."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM invoices WHERE id = ? AND user_id = ?", (invoice_id, user_id)
            )
            return cur.rowcount > 0

    def monthly_report(self, period: str, user_id: str = "") -> Optional[MonthlyReport]:
        """Báo cáo theo tháng (YYYY-MM)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM invoices WHERE user_id = ? AND substr(issue_date, 1, 7) = ?""",
                (user_id, period),
            ).fetchall()
        if not rows:
            return None
        invoices = [self._row_to_invoice(r) for r in rows]
        report = MonthlyReport(period=period)
        report.invoice_count = len(invoices)
        report.total_amount = sum(inv.total for inv in invoices)
        report.total_tax = sum(inv.tax for inv in invoices)
        report.total_discount = sum(inv.discount for inv in invoices)
        report.paid_amount = sum(inv.total for inv in invoices if inv.status == InvoiceStatus.PAID)
        report.unpaid_amount = sum(inv.total for inv in invoices if inv.status != InvoiceStatus.PAID)
        report.paid_count = sum(1 for inv in invoices if inv.status == InvoiceStatus.PAID)
        return report


# Import FieldProvenance ở cuối để tránh circular
from ..domain.models import FieldProvenance
