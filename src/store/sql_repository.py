"""SQLModel-based repositories for Invoice & Billing System.

Replaces the old raw-SQL repositories with type-safe SQLModel operations.
"""

from __future__ import annotations

import builtins
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from src.models import (
    EmailVerification,
    FieldCorrection,
    FieldProvenance,
    Invoice,
    InvoiceCreate,
    InvoiceStatus,
    InvoiceUpdate,
    LineItem,
    MonthlyReport,
    PasswordReset,
    PaymentTransaction,
    PushSubscription,
    RefreshToken,
    SharedPlaceVisit,
    TransactionAttachment,
    TxDirection,
    TxStatus,
    User,
)

logger = logging.getLogger(__name__)


class DuplicateInvoiceError(Exception):
    """Trùng invoice_number trong cùng user — không ghi đè âm thầm bản cũ."""

    def __init__(self, existing_id: str, invoice_number: str):
        super().__init__(f"Invoice số {invoice_number} đã tồn tại")
        self.existing_id = existing_id
        self.invoice_number = invoice_number


# Fields user can update via review UI
_UPDATABLE_FIELDS = {
    "invoice_number",
    "vendor",
    "buyer",
    "issue_date",
    "due_date",
    "total",
    "tax",
    "currency",
    "status",
}


def _same_value(a, b) -> bool:
    """Compare old/new values - same value means no correction."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(a).strip() == str(b).strip()


class Database:
    """Database engine and session management."""

    def __init__(self, db_path: str = "invoices.db"):
        self.db_path = db_path
        # SQLite with WAL mode for concurrent reads
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
            echo=False,
        )
        # Enable WAL mode
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA busy_timeout=5000")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    def create_tables(self):
        SQLModel.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return Session(self.engine)


class UserRepository:
    """User repository with SQLModel."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, username: str, password_hash: str, email: str = "") -> User:
        user = User(username=username, password_hash=password_hash, email=email)
        with self.db.get_session() as session:
            session.add(user)
            session.commit()
            session.refresh(user)
        return user

    def get(self, user_id: str) -> User | None:
        with self.db.get_session() as session:
            return session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        with self.db.get_session() as session:
            return session.exec(select(User).where(User.username == username)).first()

    def list_verified(self) -> list[User]:
        with self.db.get_session() as session:
            return session.exec(select(User).where(User.verified == True)).all()

    def update_password(self, user_id: str, password_hash: str) -> bool:
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if not user:
                return False
            user.password_hash = password_hash
            session.add(user)
            session.commit()
            return True

    def store_refresh_token(self, jti: str, user_id: str, expires_at: str):
        token = RefreshToken(jti=jti, user_id=user_id, expires_at=expires_at)
        with self.db.get_session() as session:
            session.add(token)
            session.commit()

    def is_refresh_token_valid(self, jti: str) -> str | None:
        with self.db.get_session() as session:
            token = session.exec(select(RefreshToken).where(RefreshToken.jti == jti)).first()
            if token and not token.revoked and token.expires_at > datetime.utcnow().isoformat():
                return token.user_id
            return None

    def revoke_refresh_token(self, jti: str):
        with self.db.get_session() as session:
            token = session.exec(select(RefreshToken).where(RefreshToken.jti == jti)).first()
            if token:
                token.revoked = True
                session.add(token)
                session.commit()

    def create_email_verification(self, user_id: str, token: str, expires_at: str):
        ev = EmailVerification(user_id=user_id, token=token, expires_at=expires_at)
        with self.db.get_session() as session:
            session.add(ev)
            session.commit()

    def consume_email_verification(self, token: str) -> str | None:
        with self.db.get_session() as session:
            ev = session.exec(
                select(EmailVerification).where(EmailVerification.token == token)
            ).first()
            if ev and not ev.used and ev.expires_at > datetime.utcnow().isoformat():
                ev.used = True
                user_id = ev.user_id
                session.add(ev)
                session.commit()
                # Mark user as verified
                user = session.get(User, user_id)
                if user:
                    user.verified = True
                    session.add(user)
                    session.commit()
                return user_id
            return None

    def create_password_reset(self, user_id: str, token: str, expires_at: str):
        pr = PasswordReset(user_id=user_id, token=token, expires_at=expires_at)
        with self.db.get_session() as session:
            session.add(pr)
            session.commit()

    def consume_password_reset(self, token: str) -> str | None:
        with self.db.get_session() as session:
            pr = session.exec(select(PasswordReset).where(PasswordReset.token == token)).first()
            if pr and not pr.used and pr.expires_at > datetime.utcnow().isoformat():
                pr.used = True
                user_id = pr.user_id
                session.add(pr)
                session.commit()
                return user_id
            return None

    def store_mfa_secret(self, user_id: str, secret: str):
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if user:
                user.mfa_secret = secret
                session.add(user)
                session.commit()

    def get_mfa_secret(self, user_id: str) -> dict | None:
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if user and user.mfa_secret:
                return {"secret": user.mfa_secret}
            return None

    def set_mfa_enabled(self, user_id: str, enabled: bool):
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if user:
                user.mfa_enabled = enabled
                session.add(user)
                session.commit()


class InvoiceRepository:
    """Invoice repository with SQLModel."""

    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.get_session() as session:
            session.exec(select(1))
        return True

    def _serialize_provenance(self, prov: dict) -> str:
        return json.dumps(
            {k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in prov.items()}
        )

    def _deserialize_provenance(self, data: str) -> dict:
        if not data:
            return {}
        try:
            raw = json.loads(data)
            return {k: FieldProvenance(**v) if isinstance(v, dict) else v for k, v in raw.items()}
        except Exception:
            return {}

    def _serialize_line_items(self, items: builtins.list[LineItem]) -> str:
        return json.dumps([item.model_dump() for item in items])

    def _deserialize_line_items(self, data: str) -> builtins.list[LineItem]:
        if not data:
            return []
        try:
            return [LineItem(**item) for item in json.loads(data)]
        except Exception:
            return []

    def _row_to_invoice(self, inv: Invoice) -> Invoice:
        """Ensure JSON fields are properly deserialized."""
        if isinstance(inv.provenance, str):
            inv.provenance = self._deserialize_provenance(inv.provenance)
        if isinstance(inv.line_items, str):
            inv.line_items = self._deserialize_line_items(inv.line_items)
        if isinstance(inv.review_history, str):
            inv.review_history = json.loads(inv.review_history)
        return inv

    def create(self, data: InvoiceCreate, user_id: str) -> Invoice:
        invoice = Invoice(
            id=uuid.uuid4().hex[:16],
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
        with self.db.get_session() as session:
            # Check for duplicates
            existing = None
            if invoice.file_checksum:
                existing = session.exec(
                    select(Invoice).where(
                        Invoice.user_id == invoice.user_id,
                        Invoice.file_checksum == invoice.file_checksum,
                        Invoice.id != invoice.id,
                    )
                ).first()
            if not existing and invoice.invoice_number not in ("", "unknown"):
                existing = session.exec(
                    select(Invoice).where(
                        Invoice.user_id == invoice.user_id,
                        Invoice.invoice_number == invoice.invoice_number,
                        Invoice.id != invoice.id,
                    )
                ).first()

            if existing:
                if existing.file_checksum == invoice.file_checksum:
                    invoice.id = existing.id
                elif invoice.invoice_number not in ("", "unknown"):
                    raise Exception(f"Duplicate invoice number: {invoice.invoice_number}")

            session.add(invoice)
            session.commit()
            session.refresh(invoice)
            return self._row_to_invoice(invoice)

    def get(self, invoice_id: str, user_id: str = "") -> Invoice | None:
        with self.db.get_session() as session:
            query = select(Invoice).where(Invoice.id == invoice_id)
            if user_id:
                query = query.where(Invoice.user_id == user_id)
            invoice = session.exec(query).first()
            return self._row_to_invoice(invoice) if invoice else None

    def list(
        self, user_id: str = "", status: InvoiceStatus | None = None, limit: int = 100
    ) -> builtins.list[Invoice]:
        with self.db.get_session() as session:
            query = select(Invoice).where(Invoice.user_id == user_id)
            if status:
                query = query.where(Invoice.status == status)
            query = query.order_by(Invoice.created_at.desc()).limit(limit)
            invoices = session.exec(query).all()
            return [self._row_to_invoice(inv) for inv in invoices]

    def update(self, invoice_id: str, data: InvoiceUpdate, user_id: str) -> Invoice | None:
        with self.db.get_session() as session:
            invoice = session.exec(
                select(Invoice).where(Invoice.id == invoice_id, Invoice.user_id == user_id)
            ).first()
            if not invoice:
                return None

            corrections = []
            for field, value in data.model_dump(exclude_unset=True).items():
                if value is None or field not in _UPDATABLE_FIELDS:
                    continue
                old = getattr(invoice, field)
                if field == "status" and isinstance(value, str):
                    value = InvoiceStatus(value)
                if _same_value(old, value):
                    continue
                setattr(invoice, field, value)

                if field != "status":
                    prov = (
                        invoice.provenance.get(field)
                        if isinstance(invoice.provenance, dict)
                        else None
                    )
                    corrections.append(
                        FieldCorrection(
                            invoice_id=invoice.id,
                            field=field,
                            old_value=old,
                            new_value=value,
                            confidence=prov.confidence if prov else None,
                            source=prov.source if prov else None,
                            corrected_by=user_id,
                            corrected_at=datetime.now(UTC).isoformat(),
                        )
                    )
                    invoice.provenance[field] = FieldProvenance(
                        value=value,
                        confidence=1.0,
                        source="manual",
                        evidence=f"user-corrected (was {old!r})",
                    )

            if corrections:
                invoice.review_history = (invoice.review_history or []) + [
                    c.model_dump() for c in corrections
                ]

            session.add(invoice)
            session.commit()
            session.refresh(invoice)
            return self._row_to_invoice(invoice)

    def delete(self, invoice_id: str, user_id: str = "") -> bool:
        with self.db.get_session() as session:
            query = select(Invoice).where(Invoice.id == invoice_id)
            if user_id:
                query = query.where(Invoice.user_id == user_id)
            invoice = session.exec(query).first()
            if invoice:
                session.delete(invoice)
                session.commit()
                return True
            return False

    def monthly_report(self, period: str, user_id: str) -> MonthlyReport | None:
        with self.db.get_session() as session:
            # SQLite: substr(issue_date, 1, 7) for YYYY-MM
            invoices = session.exec(
                select(Invoice).where(
                    Invoice.user_id == user_id, func.substr(Invoice.issue_date, 1, 7) == period
                )
            ).all()

            if not invoices:
                return None

            report = MonthlyReport(period=period)
            report.invoice_count = len(invoices)
            report.total_amount = sum(inv.total for inv in invoices)
            report.total_tax = sum(inv.tax for inv in invoices)
            report.total_discount = sum(inv.discount for inv in invoices)
            report.paid_amount = sum(
                inv.total for inv in invoices if inv.status == InvoiceStatus.PAID
            )
            report.unpaid_amount = sum(
                inv.total for inv in invoices if inv.status != InvoiceStatus.PAID
            )
            report.paid_count = sum(1 for inv in invoices if inv.status == InvoiceStatus.PAID)
            return report


class TransactionRepository:
    """Payment transaction repository with SQLModel."""

    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.get_session() as session:
            session.exec(select(1))
        return True

    def create(self, tx: PaymentTransaction) -> PaymentTransaction:
        with self.db.get_session() as session:
            # Check for duplicate external_ref
            if tx.external_ref:
                existing = session.exec(
                    select(PaymentTransaction).where(
                        PaymentTransaction.user_id == tx.user_id,
                        PaymentTransaction.external_ref == tx.external_ref,
                    )
                ).first()
                if existing:
                    return existing

            session.add(tx)
            session.commit()
            session.refresh(tx)
            return tx

    def get(self, tx_id: str, user_id: str = "") -> PaymentTransaction | None:
        with self.db.get_session() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.id == tx_id)
            if user_id:
                query = query.where(PaymentTransaction.user_id == user_id)
            return session.exec(query).first()

    def get_by_ref(self, user_id: str, external_ref: str) -> PaymentTransaction | None:
        with self.db.get_session() as session:
            return session.exec(
                select(PaymentTransaction).where(
                    PaymentTransaction.user_id == user_id,
                    PaymentTransaction.external_ref == external_ref,
                )
            ).first()

    def get_attachment_with_key(self, attachment_id: str, user_id: str) -> dict | None:
        with self.db.get_session() as session:
            attachment = session.exec(
                select(TransactionAttachment).where(
                    TransactionAttachment.id == attachment_id,
                    TransactionAttachment.user_id == user_id,
                )
            ).first()
            if not attachment:
                return None
            return {
                "id": attachment.id,
                "transaction_id": attachment.transaction_id,
                "storage_key": attachment.storage_key,
                "mime_type": attachment.mime_type,
                "size": attachment.size,
            }

    def list(
        self,
        user_id: str = "",
        status: TxStatus | None = None,
        direction: TxDirection | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 50,
    ) -> builtins.list[PaymentTransaction]:
        with self.db.get_session() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.user_id == user_id)
            if status:
                query = query.where(PaymentTransaction.status == status)
            if direction:
                query = query.where(PaymentTransaction.direction == direction)
            if start_date:
                query = query.where(PaymentTransaction.occurred_at >= start_date.isoformat())
            if end_date:
                query = query.where(PaymentTransaction.occurred_at < end_date.isoformat())
            query = query.order_by(
                PaymentTransaction.occurred_at.desc(), PaymentTransaction.created_at.desc()
            ).limit(limit)
            return session.exec(query).all()

    def set_status(
        self, tx_id: str, status: TxStatus, invoice_id: str = "", user_id: str = ""
    ) -> bool:
        with self.db.get_session() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.id == tx_id)
            if user_id:
                query = query.where(PaymentTransaction.user_id == user_id)
            tx = session.exec(query).first()
            if tx:
                tx.status = status
                tx.matched_invoice_id = invoice_id or None
                session.add(tx)
                session.commit()
                return True
            return False

    def update_details(
        self,
        tx_id: str,
        user_id: str,
        *,
        note: str | None = None,
        category: str | None = None,
        location_lat: float | None = None,
        location_lon: float | None = None,
        location_captured_at: str | None = None,
        amount: float | None = None,
        currency: str | None = None,
        direction: TxDirection | None = None,
        merchant: str | None = None,
        occurred_at: str | None = None,
        update_fields: set[str] | None = None,
    ) -> bool:
        with self.db.get_session() as session:
            tx = session.exec(
                select(PaymentTransaction).where(
                    PaymentTransaction.id == tx_id, PaymentTransaction.user_id == user_id
                )
            ).first()
            if not tx:
                return False
            for field, value in {
                "note": note, "category": category, "location_lat": location_lat,
                "location_lon": location_lon, "location_captured_at": location_captured_at,
                "amount": amount, "currency": currency, "direction": direction,
                "merchant": merchant, "occurred_at": occurred_at,
            }.items():
                if field in (update_fields or set()):
                    setattr(tx, field, value)
            session.add(tx)
            session.commit()
            return True

    def add_attachment(
        self,
        attachment_id: str,
        transaction_id: str,
        user_id: str,
        storage_key: str,
        mime_type: str,
        size: int,
    ) -> dict:
        with self.db.get_session() as session:
            tx = session.exec(
                select(PaymentTransaction).where(
                    PaymentTransaction.id == transaction_id,
                    PaymentTransaction.user_id == user_id,
                )
            ).first()
            if not tx:
                raise ValueError("Transaction does not belong to user")
            attachment = TransactionAttachment(
                id=attachment_id,
                transaction_id=transaction_id,
                user_id=user_id,
                storage_key=storage_key,
                mime_type=mime_type,
                size=size,
            )
            session.add(attachment)
            session.commit()
        return {
            "id": attachment_id,
            "transaction_id": transaction_id,
            "mime_type": mime_type,
            "size": size,
        }

    def list_attachments(self, transaction_id: str, user_id: str) -> builtins.list[dict]:
        with self.db.get_session() as session:
            attachments = session.exec(
                select(TransactionAttachment)
                .where(
                    TransactionAttachment.transaction_id == transaction_id,
                    TransactionAttachment.user_id == user_id,
                )
                .order_by(TransactionAttachment.created_at.desc())
            ).all()
            return [
                {
                    "id": a.id,
                    "transaction_id": a.transaction_id,
                    "mime_type": a.mime_type,
                    "size": a.size,
                    "created_at": a.created_at.isoformat(),
                }
                for a in attachments
            ]

    def get_attachment(self, attachment_id: str, user_id: str) -> dict | None:
        with self.db.get_session() as session:
            a = session.exec(
                select(TransactionAttachment).where(
                    TransactionAttachment.id == attachment_id,
                    TransactionAttachment.user_id == user_id,
                )
            ).first()
            if a:
                return {
                    "id": a.id,
                    "transaction_id": a.transaction_id,
                    "mime_type": a.mime_type,
                    "size": a.size,
                    "created_at": a.created_at.isoformat(),
                }
            return None

    def delete_attachment(self, attachment_id: str, user_id: str) -> dict | None:
        with self.db.get_session() as session:
            a = session.exec(
                select(TransactionAttachment).where(
                    TransactionAttachment.id == attachment_id,
                    TransactionAttachment.user_id == user_id,
                )
            ).first()
            if a:
                result = {
                    "id": a.id,
                    "transaction_id": a.transaction_id,
                    "mime_type": a.mime_type,
                    "size": a.size,
                    "created_at": a.created_at.isoformat(),
                }
                session.delete(a)
                session.commit()
                return result
            return None

    def add_shared_place_visit(
        self, user_id: str, place_name: str, place_kind: str, lat: float, lon: float
    ):
        visit = SharedPlaceVisit(
            user_id=user_id, place_name=place_name, place_kind=place_kind, lat=lat, lon=lon
        )
        with self.db.get_session() as session:
            session.add(visit)
            session.commit()

    def list_shared_places(
        self,
        lat: float,
        lon: float,
        radius: int,
        user_ids: builtins.list[str],
        exclude_user_id: str = "",
    ) -> builtins.list[dict]:
        if len(user_ids) < 3:
            return []

        import math

        lat_delta = radius / 111_000
        lon_delta = radius / max(1, 111_000 * abs(math.cos(math.radians(lat))))

        with self.db.get_session() as session:
            # Filter user_ids
            filtered_ids = [uid for uid in user_ids if uid != exclude_user_id]
            if len(filtered_ids) < 2:
                return []

            # Use raw SQL for complex spatial query
            placeholders = ",".join("?" for _ in filtered_ids)
            rows = session.exec(
                f"""SELECT place_name AS name, place_kind AS kind,
                           ROUND(AVG(lat), 2) AS lat, ROUND(AVG(lon), 2) AS lon,
                           COUNT(DISTINCT user_id) AS visitors
                    FROM shared_place_visits
                    WHERE user_id IN ({placeholders}) 
                    AND lat BETWEEN ? AND ? 
                    AND lon BETWEEN ? AND ?
                    GROUP BY place_name, place_kind, ROUND(lat, 2), ROUND(lon, 2)
                    HAVING COUNT(DISTINCT user_id) >= 2 
                    ORDER BY visitors DESC LIMIT 20""",
                (*filtered_ids, lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta),
            ).all()
            return [dict(row) for row in rows]


class PushSubscriptionRepository:
    """Push subscription repository."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, user_id: str, subscription: dict):
        sub = PushSubscription(
            id=uuid.uuid4().hex[:16],
            user_id=user_id,
            endpoint=subscription["endpoint"],
            p256dh=subscription["keys"]["p256dh"],
            auth=subscription["keys"]["auth"],
        )
        with self.db.get_session() as session:
            # Upsert by endpoint
            existing = session.exec(
                select(PushSubscription).where(PushSubscription.endpoint == sub.endpoint)
            ).first()
            if existing:
                existing.p256dh = sub.p256dh
                existing.auth = sub.auth
                session.add(existing)
            else:
                session.add(sub)
            session.commit()

    def delete(self, user_id: str, endpoint: str) -> bool:
        with self.db.get_session() as session:
            sub = session.exec(
                select(PushSubscription).where(
                    PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint
                )
            ).first()
            if sub:
                session.delete(sub)
                session.commit()
                return True
            return False


# Initialize database
_database: Database | None = None


def get_database(db_path: str = "invoices.db") -> Database:
    global _database
    if _database is None:
        _database = Database(db_path)
    return _database


def init_database(db_path: str = "invoices.db") -> Database:
    global _database
    _database = Database(db_path)
    _database.create_tables()
    return _database
