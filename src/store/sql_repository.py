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

from src.time_utils import as_utc
from src.models import (
    ApiKey,
    EmailVerification,
    FieldCorrection,
    FieldProvenance,
    GmailConnection,
    GmailPaymentAlert,
    Invoice,
    InvoiceCreate,
    InvoiceStatus,
    InvoiceTransactionLink,
    InvoiceUpdate,
    LineItem,
    MonthlyReport,
    Organization,
    OrganizationMember,
    PasswordReset,
    PaymentTransaction,
    Purchase,
    PushSubscription,
    OAuthAccount,
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
    "location_lat",
    "location_lon",
    "location_name",
    "location_address",
    "payment_lat",
    "payment_lon",
    "payment_captured_at",
    "payment_accuracy_m",
    "payment_source",
    "payment_provider",
    "merchant_poi_id",
    "merchant_name",
    "merchant_address",
    "merchant_lat",
    "merchant_lon",
    "merchant_source",
    "merchant_confidence",
    "merchant_verified_by_user",
    "purchase_context_type",
    "evidence_refs",
    "user_confirmed_location",
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

    def get_by_email(self, email: str) -> User | None:
        """Find a local account by normalized email without linking identities."""
        normalized = email.strip().casefold()
        if not normalized:
            return None
        with self.db.get_session() as session:
            return session.exec(
                select(User).where(func.lower(User.email) == normalized)
            ).first()

    def get_oauth_account(self, provider: str, provider_account_id: str) -> OAuthAccount | None:
        with self.db.get_session() as session:
            return session.exec(
                select(OAuthAccount).where(
                    OAuthAccount.provider == provider,
                    OAuthAccount.provider_account_id == provider_account_id,
                )
            ).first()

    def list_oauth_accounts(self, user_id: str) -> list[OAuthAccount]:
        with self.db.get_session() as session:
            return session.exec(select(OAuthAccount).where(OAuthAccount.user_id == user_id)).all()

    def link_oauth_account(self, user_id: str, provider: str, provider_account_id: str, email: str) -> OAuthAccount:
        account = OAuthAccount(
            user_id=user_id,
            provider=provider,
            provider_account_id=provider_account_id,
            email=email,
        )
        with self.db.get_session() as session:
            session.add(account)
            session.commit()
            session.refresh(account)
        return account

    def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        with self.db.get_session() as session:
            account = session.exec(
                select(OAuthAccount).where(
                    OAuthAccount.user_id == user_id, OAuthAccount.provider == provider
                )
            ).first()
            if not account:
                return False
            session.delete(account)
            session.commit()
            return True

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
            if token and not token.revoked and token.expires_at and as_utc(datetime.fromisoformat(token.expires_at)) > datetime.now(UTC):
                return token.user_id
            return None

    def revoke_refresh_token(self, jti: str):
        with self.db.get_session() as session:
            token = session.exec(select(RefreshToken).where(RefreshToken.jti == jti)).first()
            if token:
                token.revoked = True
                session.add(token)
                session.commit()

    def set_verified(self, user_id: str, verified: bool = True):
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if user:
                user.verified = verified
                session.add(user)
                session.commit()

    def list_verified(self) -> list[User]:
        """User đã xác minh email — dùng cho IMAP poller và gợi ý địa điểm."""
        with self.db.get_session() as session:
            return list(
                session.exec(select(User).where(User.verified == True)).all()  # noqa: E712
            )

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
            if ev and not ev.used and ev.expires_at and as_utc(datetime.fromisoformat(ev.expires_at)) > datetime.now(UTC):
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
            if pr and not pr.used and pr.expires_at and as_utc(datetime.fromisoformat(pr.expires_at)) > datetime.now(UTC):
                pr.used = True
                user_id = pr.user_id
                session.add(pr)
                session.commit()
                return user_id
            return None


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
            return raw if isinstance(raw, dict) else {}
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
        """Ensure legacy and JSON fields are usable by repository callers."""
        if inv.provenance is None:
            inv.provenance = {}
        elif isinstance(inv.provenance, str):
            inv.provenance = self._deserialize_provenance(inv.provenance)
        elif not isinstance(inv.provenance, dict):
            inv.provenance = {}
        if inv.line_items is None:
            inv.line_items = []
        elif isinstance(inv.line_items, str):
            inv.line_items = self._deserialize_line_items(inv.line_items)
        elif not isinstance(inv.line_items, list):
            inv.line_items = []
        if inv.review_history is None:
            inv.review_history = []
        elif isinstance(inv.review_history, str):
            try:
                history = json.loads(inv.review_history)
            except (TypeError, ValueError):
                history = []
            inv.review_history = history if isinstance(history, list) else []
        elif not isinstance(inv.review_history, list):
            inv.review_history = []
        return inv

    def create(self, data: InvoiceCreate, user_id: str, organization_id: str | None = None) -> Invoice:
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
            organization_id=organization_id,
        )
        return self.upsert(invoice)

    def upsert(self, invoice: Invoice) -> Invoice:
        # JSON columns must receive plain Python data, even when a repository
        # read or extractor has populated them with nested SQLModel instances.
        invoice.provenance = {
            key: value.model_dump() if hasattr(value, "model_dump") else value
            for key, value in (invoice.provenance or {}).items()
        }
        invoice.line_items = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in (invoice.line_items or [])
        ]
        with self.db.get_session() as session:
            # Check for duplicates within the personal or organization context.
            # Organization invoices are shared across members, unlike personal rows.
            context_scope = (
                Invoice.organization_id == invoice.organization_id
                if invoice.organization_id
                else (Invoice.organization_id.is_(None) & (Invoice.user_id == invoice.user_id))
            )
            existing = None
            if invoice.file_checksum:
                existing = session.exec(
                    select(Invoice).where(
                        context_scope,
                        Invoice.file_checksum == invoice.file_checksum,
                        Invoice.id != invoice.id,
                    )
                ).first()
            if not existing and invoice.invoice_number not in ("", "unknown"):
                existing = session.exec(
                    select(Invoice).where(
                        context_scope,
                        Invoice.invoice_number == invoice.invoice_number,
                        Invoice.id != invoice.id,
                    )
                ).first()

            if existing:
                if existing.file_checksum and existing.file_checksum == invoice.file_checksum:
                    # Same file: return the persisted row instead of attaching a
                    # second ORM object with its id (which violates the PK).
                    return self._row_to_invoice(existing)
                elif invoice.invoice_number not in ("", "unknown"):
                    # Lỗi định kiểu (có existing_id/invoice_number) để cả /upload,
                    # /invoices và /payments/camera-upload đều báo DUPLICATE_INVOICE
                    # 409 theo cùng một luật — trước đó raise Exception chung.
                    raise DuplicateInvoiceError(existing.id, invoice.invoice_number)

            session.add(invoice)
            session.commit()
            session.refresh(invoice)
            return self._row_to_invoice(invoice)

    def get(self, invoice_id: str, user_id: str = "", organization_id: str | None = None) -> Invoice | None:
        """Org context -> lọc theo organization_id (membership đã kiểm ở route);
        cá nhân -> lọc theo user_id (spec multi-tenant mục 3)."""
        with self.db.get_session() as session:
            query = select(Invoice).where(Invoice.id == invoice_id)
            if organization_id:
                query = query.where(Invoice.organization_id == organization_id)
            elif user_id:
                query = query.where(Invoice.user_id == user_id)
            invoice = session.exec(query).first()
            return self._row_to_invoice(invoice) if invoice else None

    def list(
        self,
        user_id: str = "",
        status: InvoiceStatus | None = None,
        limit: int = 100,
        organization_id: str | None = None,
    ) -> builtins.list[Invoice]:
        with self.db.get_session() as session:
            query = select(Invoice)
            if organization_id:
                query = query.where(Invoice.organization_id == organization_id)
            else:
                query = query.where(Invoice.user_id == user_id)
            if status:
                query = query.where(Invoice.status == status)
            query = query.order_by(Invoice.created_at.desc()).limit(limit)
            invoices = session.exec(query).all()
            return [self._row_to_invoice(inv) for inv in invoices]

    def update(
        self, invoice_id: str, data: InvoiceUpdate, user_id: str,
        organization_id: str | None = None,
    ) -> Invoice | None:
        with self.db.get_session() as session:
            query = select(Invoice).where(Invoice.id == invoice_id)
            if organization_id:
                query = query.where(Invoice.organization_id == organization_id)
            else:
                query = query.where(Invoice.user_id == user_id)
            invoice = session.exec(query).first()
            if not invoice:
                return None
            self._row_to_invoice(invoice)

            corrections = []
            for field, value in data.model_dump(exclude_unset=True).items():
                if value is None or field not in _UPDATABLE_FIELDS:
                    continue
                old = getattr(invoice, field)
                if field == "status" and isinstance(value, str):
                    value = InvoiceStatus(value)
                if _same_value(old, value):
                    continue
                if field == "invoice_number" and value not in ("", "unknown"):
                    duplicate_query = select(Invoice).where(
                        Invoice.invoice_number == value,
                        Invoice.id != invoice.id,
                    )
                    if organization_id:
                        duplicate_query = duplicate_query.where(
                            Invoice.organization_id == organization_id
                        )
                    else:
                        duplicate_query = duplicate_query.where(
                            Invoice.user_id == user_id,
                            Invoice.organization_id.is_(None),
                        )
                    duplicate = session.exec(duplicate_query).first()
                    if duplicate:
                        raise DuplicateInvoiceError(duplicate.id, value)
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
                            confidence=prov.get("confidence") if prov else None,
                            source=prov.get("source") if prov else None,
                            corrected_by=user_id,
                            corrected_at=datetime.now(UTC).isoformat(),
                        )
                    )
                    new_prov = {}
                    for k, v in (invoice.provenance or {}).items():
                        if hasattr(v, "model_dump"):
                            new_prov[k] = v.model_dump()
                        elif isinstance(v, dict):
                            new_prov[k] = v
                        else:
                            new_prov[k] = v
                    new_prov[field] = {
                        "value": value,
                        "confidence": 1.0,
                        "source": "manual",
                        "evidence": f"user-corrected (was {old!r})",   # } closes {old!r} inside f-string
                    }   # <-- THIS } closes { from line 366
                    invoice.provenance = new_prov

            if corrections:
                invoice.review_history = (invoice.review_history or []) + [
                    c.model_dump() for c in corrections
                ]

            session.add(invoice)
            session.commit()
            session.refresh(invoice)
            return self._row_to_invoice(invoice)

    def delete(self, invoice_id: str, user_id: str = "", organization_id: str | None = None) -> bool:
        with self.db.get_session() as session:
            query = select(Invoice).where(Invoice.id == invoice_id)
            if organization_id:
                query = query.where(Invoice.organization_id == organization_id)
            elif user_id:
                query = query.where(Invoice.user_id == user_id)
            invoice = session.exec(query).first()
            if invoice:
                session.delete(invoice)
                session.commit()
                return True
            return False

    def monthly_report(
        self, period: str, user_id: str, organization_id: str | None = None
    ) -> MonthlyReport | None:
        with self.db.get_session() as session:
            # SQLite: substr(issue_date, 1, 7) for YYYY-MM
            period_cond = func.substr(Invoice.issue_date, 1, 7) == period
            if organization_id:
                invoices = session.exec(
                    select(Invoice).where(
                        Invoice.organization_id == organization_id, period_cond
                    )
                ).all()
            else:
                invoices = session.exec(
                    select(Invoice).where(Invoice.user_id == user_id, period_cond)
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

    def get(self, tx_id: str, user_id: str = "", organization_id: str | None = None) -> PaymentTransaction | None:
        """Org context -> lọc theo organization_id; cá nhân -> theo user_id."""
        with self.db.get_session() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.id == tx_id)
            if organization_id:
                query = query.where(PaymentTransaction.organization_id == organization_id)
            elif user_id:
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
        organization_id: str | None = None,
    ) -> builtins.list[PaymentTransaction]:
        with self.db.get_session() as session:
            query = select(PaymentTransaction)
            if organization_id:
                query = query.where(PaymentTransaction.organization_id == organization_id)
            else:
                query = query.where(PaymentTransaction.user_id == user_id)
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
        self, tx_id: str, status: TxStatus, invoice_id: str = "", user_id: str = "",
        organization_id: str | None = None,
    ) -> bool:
        with self.db.get_session() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.id == tx_id)
            if organization_id:
                query = query.where(PaymentTransaction.organization_id == organization_id)
            elif user_id:
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
        payment_lat: float | None = None,
        payment_lon: float | None = None,
        payment_captured_at: str | None = None,
        payment_accuracy_m: float | None = None,
        payment_source: str | None = None,
        payment_provider: str | None = None,
        merchant_poi_id: str | None = None,
        merchant_name: str | None = None,
        merchant_address: str | None = None,
        merchant_lat: float | None = None,
        merchant_lon: float | None = None,
        merchant_source: str | None = None,
        merchant_confidence: float | None = None,
        merchant_verified_by_user: bool | None = None,
        purchase_context_type: str | None = None,
        evidence_refs: list[str] | None = None,
        user_confirmed_location: bool | None = None,
        amount: float | None = None,
        currency: str | None = None,
        direction: TxDirection | None = None,
        merchant: str | None = None,
        occurred_at: str | None = None,
        update_fields: set[str] | None = None,
        organization_id: str | None = None,
    ) -> bool:
        with self.db.get_session() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.id == tx_id)
            if organization_id:
                query = query.where(PaymentTransaction.organization_id == organization_id)
            else:
                query = query.where(PaymentTransaction.user_id == user_id)
            tx = session.exec(query).first()
            if not tx:
                return False
            for field, value in {
                "note": note, "category": category, "location_lat": location_lat,
                "location_lon": location_lon, "location_captured_at": location_captured_at,
                "payment_lat": payment_lat, "payment_lon": payment_lon,
                "payment_captured_at": payment_captured_at,
                "payment_accuracy_m": payment_accuracy_m, "payment_source": payment_source,
                "payment_provider": payment_provider,
                "merchant_poi_id": merchant_poi_id, "merchant_name": merchant_name,
                "merchant_address": merchant_address, "merchant_lat": merchant_lat,
                "merchant_lon": merchant_lon, "merchant_source": merchant_source,
                "merchant_confidence": merchant_confidence,
                "merchant_verified_by_user": merchant_verified_by_user,
                "purchase_context_type": purchase_context_type,
                "evidence_refs": evidence_refs,
                "user_confirmed_location": user_confirmed_location,
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


class PurchaseRepository:
    """Repository for manual purchases captured from photos."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, purchase: Purchase) -> Purchase:
        with self.db.get_session() as session:
            session.add(purchase)
            session.commit()
            session.refresh(purchase)
            return purchase

    def get(self, purchase_id: str, user_id: str = "", organization_id: str | None = None) -> Purchase | None:
        with self.db.get_session() as session:
            query = select(Purchase).where(Purchase.id == purchase_id)
            if user_id:
                query = query.where(Purchase.user_id == user_id)
            if organization_id is not None:
                query = query.where(Purchase.organization_id == organization_id)
            return session.exec(query).first()

    def list(self, user_id: str, organization_id: str | None = None, limit: int = 200) -> list[Purchase]:
        with self.db.get_session() as session:
            query = select(Purchase).where(Purchase.user_id == user_id)
            if organization_id is not None:
                query = query.where(Purchase.organization_id == organization_id)
            query = query.order_by(Purchase.created_at.desc()).limit(limit)
            return list(session.exec(query).all())

    def delete(self, purchase_id: str, user_id: str) -> bool:
        with self.db.get_session() as session:
            purchase = session.exec(
                select(Purchase).where(Purchase.id == purchase_id, Purchase.user_id == user_id)
            ).first()
            if not purchase:
                return False
            session.delete(purchase)
            session.commit()
            return True


class InvoiceTransactionLinkRepository:
    """Liên kết nhiều-nhiều giao dịch <-> hóa đơn (ghép gộp/trả góp)."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        user_id: str,
        transaction_id: str,
        invoice_id: str,
        amount: float = 0.0,
        match_reason: str = "manual",
    ) -> InvoiceTransactionLink:
        link = InvoiceTransactionLink(
            id=uuid.uuid4().hex[:16],
            user_id=user_id,
            transaction_id=transaction_id,
            invoice_id=invoice_id,
            amount=amount,
            match_reason=match_reason,
        )
        with self.db.get_session() as session:
            existing = session.exec(
                select(InvoiceTransactionLink).where(
                    InvoiceTransactionLink.transaction_id == transaction_id,
                    InvoiceTransactionLink.invoice_id == invoice_id,
                )
            ).first()
            if existing:
                existing.amount = amount
                existing.match_reason = match_reason
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            session.add(link)
            session.commit()
            session.refresh(link)
            return link

    def list_for_transaction(
        self, transaction_id: str, user_id: str = ""
    ) -> builtins.list[InvoiceTransactionLink]:
        with self.db.get_session() as session:
            query = select(InvoiceTransactionLink).where(
                InvoiceTransactionLink.transaction_id == transaction_id
            )
            if user_id:
                query = query.where(InvoiceTransactionLink.user_id == user_id)
            return session.exec(query).all()

    def list_for_invoice(
        self, invoice_id: str, user_id: str = ""
    ) -> builtins.list[InvoiceTransactionLink]:
        with self.db.get_session() as session:
            query = select(InvoiceTransactionLink).where(
                InvoiceTransactionLink.invoice_id == invoice_id
            )
            if user_id:
                query = query.where(InvoiceTransactionLink.user_id == user_id)
            return session.exec(query).all()

    def delete(self, link_id: str, user_id: str = "") -> bool:
        with self.db.get_session() as session:
            query = select(InvoiceTransactionLink).where(InvoiceTransactionLink.id == link_id)
            if user_id:
                query = query.where(InvoiceTransactionLink.user_id == user_id)
            link = session.exec(query).first()
            if link:
                session.delete(link)
                session.commit()
                return True
            return False


class GmailRepository:
    """Ownership-safe persistence for Gmail connections and payment alerts."""

    def __init__(self, db: Database):
        self.db = db

    def create_connection(
        self,
        user_id: str,
        gmail_account_id: str,
        *,
        email: str = "",
        encrypted_refresh_token: str = "",
    ) -> GmailConnection:
        """Create or return the user's connection for one Gmail account."""
        with self.db.get_session() as session:
            connection = session.exec(
                select(GmailConnection).where(
                    GmailConnection.user_id == user_id,
                    GmailConnection.gmail_account_id == gmail_account_id,
                )
            ).first()
            if connection:
                if email:
                    connection.email = email
                if encrypted_refresh_token:
                    connection.encrypted_refresh_token = encrypted_refresh_token
                connection.updated_at = datetime.now(UTC)
            else:
                connection = GmailConnection(
                    user_id=user_id,
                    gmail_account_id=gmail_account_id,
                    email=email,
                    encrypted_refresh_token=encrypted_refresh_token,
                )
            session.add(connection)
            session.commit()
            session.refresh(connection)
            return connection

    def get_connection(self, connection_id: str, user_id: str) -> GmailConnection | None:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailConnection).where(
                    GmailConnection.id == connection_id,
                    GmailConnection.user_id == user_id,
                )
            ).first()

    def get_connection_by_account(
        self, user_id: str, gmail_account_id: str
    ) -> GmailConnection | None:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailConnection).where(
                    GmailConnection.user_id == user_id,
                    GmailConnection.gmail_account_id == gmail_account_id,
                )
            ).first()

    def list_connections(self, user_id: str) -> builtins.list[GmailConnection]:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailConnection)
                .where(GmailConnection.user_id == user_id)
                .order_by(GmailConnection.created_at.desc())
            ).all()

    def list_all_connections(self) -> builtins.list[GmailConnection]:
        """List Gmail connections for trusted maintenance workers."""
        with self.db.get_session() as session:
            return session.exec(
                select(GmailConnection).order_by(GmailConnection.updated_at.asc())
            ).all()

    def update_cursor(self, connection_id: str, user_id: str, history_id: str) -> GmailConnection | None:
        with self.db.get_session() as session:
            connection = self._owned_connection(session, connection_id, user_id)
            if not connection:
                return None
            connection.history_id = history_id
            connection.updated_at = datetime.now(UTC)
            session.add(connection)
            session.commit()
            session.refresh(connection)
            return connection

    def update_watch(
        self,
        connection_id: str,
        user_id: str,
        *,
        history_id: str | None = None,
        watch_expiration: datetime | None = None,
        watch_topic: str | None = None,
    ) -> GmailConnection | None:
        with self.db.get_session() as session:
            connection = self._owned_connection(session, connection_id, user_id)
            if not connection:
                return None
            if history_id is not None:
                connection.history_id = history_id
            if watch_expiration is not None:
                connection.watch_expiration = watch_expiration
            if watch_topic is not None:
                connection.watch_topic = watch_topic
            connection.updated_at = datetime.now(UTC)
            session.add(connection)
            session.commit()
            session.refresh(connection)
            return connection

    def upsert_payment_alert(
        self,
        user_id: str,
        connection_id: str,
        gmail_message_id: str,
        *,
        thread_id: str = "",
        sender: str = "",
        subject: str = "",
        received_at: datetime | None = None,
        raw_payload: dict | None = None,
    ) -> GmailPaymentAlert | None:
        """Insert an alert once per connection/message, updating duplicate metadata."""
        with self.db.get_session() as session:
            if not self._owned_connection(session, connection_id, user_id):
                return None
            alert = session.exec(
                select(GmailPaymentAlert).where(
                    GmailPaymentAlert.connection_id == connection_id,
                    GmailPaymentAlert.gmail_message_id == gmail_message_id,
                )
            ).first()
            if alert:
                alert.thread_id = thread_id
                alert.sender = sender
                alert.subject = subject
                alert.received_at = received_at
                if raw_payload is not None:
                    alert.raw_payload = raw_payload
            else:
                alert = GmailPaymentAlert(
                    user_id=user_id,
                    connection_id=connection_id,
                    gmail_message_id=gmail_message_id,
                    thread_id=thread_id,
                    sender=sender,
                    subject=subject,
                    received_at=received_at,
                    raw_payload=raw_payload or {},
                )
            session.add(alert)
            session.commit()
            session.refresh(alert)
            return alert

    def get_payment_alert(self, alert_id: str, user_id: str) -> GmailPaymentAlert | None:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailPaymentAlert).where(
                    GmailPaymentAlert.id == alert_id,
                    GmailPaymentAlert.user_id == user_id,
                )
            ).first()

    def get_payment_alert_by_message(
        self, connection_id: str, gmail_message_id: str, user_id: str
    ) -> GmailPaymentAlert | None:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailPaymentAlert).where(
                    GmailPaymentAlert.connection_id == connection_id,
                    GmailPaymentAlert.gmail_message_id == gmail_message_id,
                    GmailPaymentAlert.user_id == user_id,
                )
            ).first()

    def delete_payment_alert(self, alert_id: str, user_id: str) -> bool:
        with self.db.get_session() as session:
            alert = session.exec(
                select(GmailPaymentAlert).where(
                    GmailPaymentAlert.id == alert_id,
                    GmailPaymentAlert.user_id == user_id,
                )
            ).first()
            if not alert:
                return False
            session.delete(alert)
            session.commit()
            return True

    def list_payment_alerts(
        self, connection_id: str, user_id: str, limit: int = 100
    ) -> builtins.list[GmailPaymentAlert]:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailPaymentAlert)
                .where(
                    GmailPaymentAlert.connection_id == connection_id,
                    GmailPaymentAlert.user_id == user_id,
                )
                .order_by(GmailPaymentAlert.received_at.desc(), GmailPaymentAlert.created_at.desc())
                .limit(limit)
            ).all()

    def get_connection_unscoped(self, connection_id: str) -> GmailConnection | None:
        with self.db.get_session() as session:
            return session.get(GmailConnection, connection_id)

    def list_connections_by_email(self, email: str) -> builtins.list[GmailConnection]:
        with self.db.get_session() as session:
            return session.exec(
                select(GmailConnection).where(GmailConnection.email == email)
            ).all()

    def disconnect(self, connection_id: str, user_id: str) -> bool:
        """Delete a connection and all its alerts only when owned by the user."""
        with self.db.get_session() as session:
            connection = self._owned_connection(session, connection_id, user_id)
            if not connection:
                return False
            alerts = session.exec(
                select(GmailPaymentAlert).where(
                    GmailPaymentAlert.connection_id == connection_id,
                    GmailPaymentAlert.user_id == user_id,
                )
            ).all()
            for alert in alerts:
                session.delete(alert)
            session.flush()
            session.delete(connection)
            session.commit()
            return True

    def clear_credentials(self, connection_id: str) -> bool:
        """Disable a connection after Google revokes or invalidates its grant."""
        with self.db.get_session() as session:
            connection = session.get(GmailConnection, connection_id)
            if not connection:
                return False
            connection.encrypted_refresh_token = ""
            connection.history_id = None
            connection.watch_expiration = None
            connection.watch_topic = ""
            connection.updated_at = datetime.now(UTC)
            session.add(connection)
            session.commit()
            return True

    @staticmethod
    def _owned_connection(
        session: Session, connection_id: str, user_id: str
    ) -> GmailConnection | None:
        return session.exec(
            select(GmailConnection).where(
                GmailConnection.id == connection_id,
                GmailConnection.user_id == user_id,
            )
        ).first()


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

    def list_for_user(self, user_id: str) -> builtins.list[dict]:
        """Subscription dict dạng webpush subscription_info (endpoint + keys)."""
        with self.db.get_session() as session:
            subs = session.exec(
                select(PushSubscription).where(PushSubscription.user_id == user_id)
            ).all()
            return [
                {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}
                for s in subs
            ]


class ApiKeyRepository:
    """API key bên thứ ba — tra cứu theo hash, không lưu plaintext."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, key: ApiKey) -> ApiKey:
        with self.db.get_session() as session:
            session.add(key)
            session.commit()
            session.refresh(key)
            return key

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        with self.db.get_session() as session:
            return session.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()

    def list_for_user(self, user_id: str) -> builtins.list[ApiKey]:
        with self.db.get_session() as session:
            return session.exec(
                select(ApiKey)
                .where(ApiKey.user_id == user_id)
                .order_by(ApiKey.created_at.desc())
            ).all()

    def get(self, key_id: str, user_id: str) -> ApiKey | None:
        with self.db.get_session() as session:
            return session.exec(
                select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
            ).first()

    def revoke(self, key_id: str, user_id: str) -> bool:
        """Hủy key (soft: is_active=False) — giữ lại để audit."""
        with self.db.get_session() as session:
            key = session.exec(
                select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
            ).first()
            if not key:
                return False
            key.is_active = False
            session.add(key)
            session.commit()
            return True

    def touch_last_used(self, key_id: str) -> None:
        with self.db.get_session() as session:
            key = session.exec(select(ApiKey).where(ApiKey.id == key_id)).first()
            if key:
                key.last_used_at = datetime.now(UTC)
                session.add(key)
                session.commit()


class OrganizationRepository:
    """Organization + membership (multi-tenant, xem docs/multi-tenant-spec.md)."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, name: str, slug: str, owner_user_id: str) -> tuple[Organization, OrganizationMember]:
        with self.db.get_session() as session:
            org = Organization(name=name, slug=slug, owner_user_id=owner_user_id)
            session.add(org)
            session.commit()
            member = OrganizationMember(
                organization_id=org.id, user_id=owner_user_id, role="owner"
            )
            session.add(member)
            session.commit()
            # Commit thứ hai expire org — refresh lại sau commit cuối để đối tượng
            # trả ra còn nguyên thuộc tính ngoài session.
            session.refresh(org)
            session.refresh(member)
            return org, member

    def get(self, org_id: str) -> Organization | None:
        with self.db.get_session() as session:
            return session.get(Organization, org_id)

    def get_membership(self, org_id: str, user_id: str) -> OrganizationMember | None:
        with self.db.get_session() as session:
            return session.exec(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.user_id == user_id,
                )
            ).first()

    def list_for_user(self, user_id: str) -> builtins.list[tuple[Organization, str]]:
        """(org, role) của mọi org user thuộc về."""
        with self.db.get_session() as session:
            rows = session.exec(
                select(Organization, OrganizationMember.role)
                .join(
                    OrganizationMember,
                    OrganizationMember.organization_id == Organization.id,
                )
                .where(OrganizationMember.user_id == user_id)
                .order_by(Organization.created_at)
            ).all()
            return [(org, role) for org, role in rows]

    def list_members(self, org_id: str) -> builtins.list[dict]:
        with self.db.get_session() as session:
            rows = session.exec(
                select(OrganizationMember, User.username)
                .join(User, User.id == OrganizationMember.user_id)
                .where(OrganizationMember.organization_id == org_id)
                .order_by(OrganizationMember.joined_at)
            ).all()
            return [
                {
                    "user_id": m.user_id,
                    "username": username,
                    "role": m.role,
                    "joined_at": m.joined_at,
                }
                for m, username in rows
            ]

    def add_member(self, org_id: str, user_id: str, role: str) -> OrganizationMember:
        with self.db.get_session() as session:
            member = OrganizationMember(organization_id=org_id, user_id=user_id, role=role)
            session.add(member)
            session.commit()
            session.refresh(member)
            return member

    def update_role(self, org_id: str, user_id: str, role: str) -> bool:
        with self.db.get_session() as session:
            member = self._member(session, org_id, user_id)
            if not member:
                return False
            member.role = role
            session.add(member)
            session.commit()
            return True

    def remove_member(self, org_id: str, user_id: str) -> bool:
        with self.db.get_session() as session:
            member = self._member(session, org_id, user_id)
            if not member:
                return False
            session.delete(member)
            session.commit()
            return True

    def delete(self, org_id: str) -> bool:
        """Xóa org + members; hóa đơn/giao dịch thuộc org trở về cá nhân
        (organization_id = NULL theo user_id chủ bản ghi)."""
        with self.db.get_session() as session:
            org = session.get(Organization, org_id)
            if not org:
                return False
            members = session.exec(
                select(OrganizationMember).where(OrganizationMember.organization_id == org_id)
            ).all()
            for m in members:
                session.delete(m)
            for model in (Invoice, PaymentTransaction):
                rows = session.exec(
                    select(model).where(model.organization_id == org_id)
                ).all()
                for row in rows:
                    row.organization_id = None
                    session.add(row)
            session.delete(org)
            session.commit()
            return True

    @staticmethod
    def _member(session: Session, org_id: str, user_id: str) -> OrganizationMember | None:
        return session.exec(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        ).first()



def _ensure_purchase_table(db: Database) -> None:
    """Idempotent creation of purchases table (phase 1 manual capture)."""
    with db.engine.begin() as conn:
        conn.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS purchases (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                organization_id TEXT,
                item TEXT DEFAULT '',
                category TEXT DEFAULT '',
                merchant TEXT DEFAULT '',
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'VND',
                occurred_at TEXT,
                notes TEXT DEFAULT '',
                source_file TEXT DEFAULT '',
                file_checksum TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                mime_type TEXT DEFAULT '',
                location_lat REAL,
                location_lon REAL,
                location_name TEXT,
                location_address TEXT,
                location_captured_at TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_purchases_org ON purchases(organization_id)")



# Initialize database
_database: Database | None = None


def get_database(db_path: str = "invoices.db") -> Database:
    global _database
    if _database is None:
        _database = Database(db_path)
    return _database


def _ensure_location_columns(db: Database) -> None:
    """Idempotent ALTER: thêm cột location mới (ADR-0002) cho invoices,
    payment_transactions, purchases. Backward compat: không xóa cột cũ."""
    new_cols = {
        "invoices": [
            "payment_lat REAL", "payment_lon REAL", "payment_captured_at TEXT",
            "payment_accuracy_m REAL", "payment_source TEXT", "payment_provider TEXT",
            "merchant_poi_id TEXT", "merchant_name TEXT", "merchant_address TEXT",
            "merchant_lat REAL", "merchant_lon REAL", "merchant_source TEXT",
            "merchant_confidence REAL", "merchant_verified_by_user INTEGER DEFAULT 0",
            "purchase_context_type TEXT", "evidence_refs TEXT", "user_confirmed_location INTEGER DEFAULT 0",
        ],
        "payment_transactions": [
            "payment_lat REAL", "payment_lon REAL", "payment_captured_at TEXT",
            "payment_accuracy_m REAL", "payment_source TEXT", "payment_provider TEXT",
            "merchant_poi_id TEXT", "merchant_name TEXT", "merchant_address TEXT",
            "merchant_lat REAL", "merchant_lon REAL", "merchant_source TEXT",
            "merchant_confidence REAL", "merchant_verified_by_user INTEGER DEFAULT 0",
            "purchase_context_type TEXT", "evidence_refs TEXT", "user_confirmed_location INTEGER DEFAULT 0",
        ],
        "purchases": [
            "payment_lat REAL", "payment_lon REAL", "payment_captured_at TEXT",
            "payment_accuracy_m REAL", "payment_source TEXT", "payment_provider TEXT",
            "merchant_poi_id TEXT", "merchant_name TEXT", "merchant_address TEXT",
            "merchant_lat REAL", "merchant_lon REAL", "merchant_source TEXT",
            "merchant_confidence REAL", "merchant_verified_by_user INTEGER DEFAULT 0",
            "purchase_context_type TEXT", "evidence_refs TEXT", "user_confirmed_location INTEGER DEFAULT 0",
        ],
    }
    with db.engine.begin() as conn:
        for table, cols in new_cols.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for coldef in cols:
                colname = coldef.split()[0]
                if colname not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {coldef}")


def _ensure_sync_tables(db: Database) -> None:
    """Idempotent creation of sync outbox/cursor tables (Phase 1)."""
    with db.engine.begin() as conn:
        conn.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS sync_outbox (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                synced_at TEXT
            )"""
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_sync_outbox_user_status ON sync_outbox(user_id, status)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_sync_outbox_created ON sync_outbox(created_at)"
        )
        conn.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS sync_cursor (
                user_id TEXT PRIMARY KEY,
                last_sync_at TEXT NOT NULL,
                server_cursor TEXT
            )"""
        )


def _ensure_invoice_mime_column(db: Database) -> None:
    """Idempotent ALTER: thêm cột mime_type cho invoices (nếu DB cũ chưa có)."""
    with db.engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(invoices)")}
        if "mime_type" not in existing:
            conn.exec_driver_sql("ALTER TABLE invoices ADD COLUMN mime_type TEXT DEFAULT ''")


def init_database(db_path: str = "invoices.db") -> Database:
    global _database
    _database = Database(db_path)
    _database.create_tables()
    _ensure_multi_tenant_columns(_database)
    _ensure_purchase_table(_database)
    _ensure_location_columns(_database)
    _ensure_invoice_mime_column(_database)
    _ensure_sync_tables(_database)
    _backfill_location_data(_database)
    return _database


def _ensure_multi_tenant_columns(db: Database) -> None:
    """create_all không thêm cột vào bảng đã có — ALTER idempotent khi thiếu
    (DB cũ mở lại sau nâng cấp vẫn chạy được mà không cần script tay)."""
    with db.engine.begin() as conn:
        for table in ("invoices", "payment_transactions"):
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if "organization_id" not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN organization_id TEXT")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_invoices_org ON invoices(organization_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_payment_transactions_org "
            "ON payment_transactions(organization_id)"
        )


def _backfill_location_data(db: Database) -> None:
    """Backfill dữ liệu từ cột location_* cũ sang cột mới (ADR-0002).

    - Nếu có location_captured_at -> payment_lat/lon/captured_at
    - Nếu không -> merchant_lat/lon (coi là merchant location)
    Idempotent: chỉ backfill khi cột mới đang NULL.
    """
    tables = ("invoices", "payment_transactions", "purchases")
    with db.engine.begin() as conn:
        for table in tables:
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not {"location_lat", "location_lon"} <= cols:
                continue
            has_captured = "location_captured_at" in cols
            has_location_name = "location_name" in cols
            has_location_address = "location_address" in cols
            # payment location backfill (có captured_at) — chỉ khi bảng có cột captured_at
            if has_captured:
                conn.exec_driver_sql(
                    f"""UPDATE {table}
                        SET payment_lat = location_lat,
                            payment_lon = location_lon,
                            payment_captured_at = location_captured_at
                        WHERE payment_lat IS NULL
                          AND location_lat IS NOT NULL
                          AND location_captured_at IS NOT NULL
                    """
                )
            # merchant location backfill
            set_parts = ["merchant_lat = location_lat", "merchant_lon = location_lon"]
            if has_location_name:
                set_parts.append("merchant_name = COALESCE(NULLIF(merchant_name, ''), location_name)")
            if has_location_address:
                set_parts.append("merchant_address = COALESCE(NULLIF(merchant_address, ''), location_address)")
            where = "merchant_lat IS NULL AND location_lat IS NOT NULL"
            if has_captured:
                where += " AND location_captured_at IS NULL"
            conn.exec_driver_sql(
                f"""UPDATE {table}
                    SET {', '.join(set_parts)}
                    WHERE {where}
                """
            )
