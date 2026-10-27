"""SQLModel models for Invoice & Billing System.

Migrated from Pydantic models to SQLModel for type-safe database operations.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped
from sqlmodel import JSON, Column, Field, Relationship, SQLModel


# --- Enums (same as before) ---
class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    REVIEW = "review"
    FINALIZED = "finalized"
    FAILED = "failed"


class InvoiceStatus(str, Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class TxDirection(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TxStatus(str, Enum):
    PENDING_RECEIPT = "pending_receipt"
    MATCHED = "matched"
    DISMISSED = "dismissed"
    RECORDED = "recorded"


# --- Field Provenance (embedded JSON) ---
class FieldProvenance(SQLModel):
    """Provenance for a field — value, confidence, source, evidence."""

    value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "unverified"
    page: int | None = None
    bbox: list[float] | None = None
    text_span: str | None = None
    evidence: str | None = None


class LineItem(SQLModel):
    """A line item in an invoice."""

    description: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0
    provenance: FieldProvenance | None = None


# --- Database Models ---
class User(SQLModel, table=True):
    """User table."""

    __tablename__ = "users"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    username: str = Field(index=True, unique=True, min_length=3, max_length=64)
    password_hash: str = Field(min_length=60)  # bcrypt hash
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified: bool = False
    email: str = Field(default="", max_length=254)

    # Relationships
    invoices: Mapped[list["Invoice"]] = Relationship(back_populates="user")
    transactions: Mapped[list["PaymentTransaction"]] = Relationship(back_populates="user")
    push_subscriptions: Mapped[list["PushSubscription"]] = Relationship(back_populates="user")


class Invoice(SQLModel, table=True):
    """Invoice table — schema versioned."""

    __tablename__ = "invoices"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)

    # Core fields
    invoice_number: str = Field(default="unknown", index=True)
    vendor: str = Field(default="unknown")
    buyer: str = ""
    issue_date: str | None = None
    due_date: str | None = None
    currency: str = "USD"
    subtotal: float = 0.0
    total: float = 0.0
    tax: float = 0.0
    tax_rate: float = 0.0
    discount: float = 0.0
    amount_due: float = 0.0
    status: InvoiceStatus = Field(
        default=InvoiceStatus.UNPAID, sa_column=Column("status", type_=SQLEnum(InvoiceStatus))
    )
    source_file: str = ""
    raw_snippet: str = ""
    confidence: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Schema versioned fields
    schema_version: int = 2
    supplier_tax_id: str = ""
    customer_tax_id: str = ""
    line_items: list[LineItem] = Field(default_factory=list, sa_column=Column(JSON))
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict, sa_column=Column(JSON))
    job_status: JobStatus = Field(
        default=JobStatus.REVIEW, sa_column=Column("job_status", type_=SQLEnum(JobStatus))
    )
    finalized_at: datetime | None = None
    finalized_by: str | None = None
    file_checksum: str = ""
    file_size: int = 0
    page_count: int | None = None
    extraction_provider: str = ""
    review_history: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # Location/route fields (new)
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = None
    location_address: str | None = None
    transport_mode: str | None = None
    route_sequence: int | None = None

    # Relationships
    user: Mapped["User"] = Relationship(back_populates="invoices")
    links: Mapped[list["InvoiceTransactionLink"]] = Relationship(
        back_populates="invoice",
        sa_relationship_kwargs={
            "foreign_keys": "InvoiceTransactionLink.invoice_id",
            "cascade": "all, delete-orphan",
        },
    )


class InvoiceTransactionLink(SQLModel, table=True):
    """Liên kết giao dịch <-> hóa đơn (many-to-many).

    PaymentTransaction.matched_invoice_id (1-1) vẫn giữ cho tự động ghép;
    bảng này lưu các liên kết bổ sung: một giao dịch thanh toán nhiều hóa đơn,
    hoặc một hóa đơn trả nhiều lần (đặt cọc, tất toán). amount = phần tiền giao
    dịch được quy cho hóa đơn này.
    """

    __tablename__ = "invoice_transaction_links"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    transaction_id: str = Field(foreign_key="payment_transactions.id", index=True)
    invoice_id: str = Field(foreign_key="invoices.id", index=True)
    amount: float = 0.0
    match_reason: str = "manual"  # manual | partial | combination
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    transaction: Mapped["PaymentTransaction"] = Relationship(
        back_populates="links",
        sa_relationship_kwargs={"foreign_keys": "InvoiceTransactionLink.transaction_id"},
    )
    invoice: Mapped["Invoice"] = Relationship(
        back_populates="links",
        sa_relationship_kwargs={"foreign_keys": "InvoiceTransactionLink.invoice_id"},
    )


class PaymentTransaction(SQLModel, table=True):
    """Payment transaction table."""

    __tablename__ = "payment_transactions"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    amount: float = 0.0
    currency: str = "VND"
    direction: TxDirection = Field(
        default=TxDirection.DEBIT, sa_column=Column("direction", type_=SQLEnum(TxDirection))
    )
    merchant: str = ""
    occurred_at: str | None = None
    source: str = "sms"
    external_ref: str = Field(default="", index=True)
    status: TxStatus = Field(
        default=TxStatus.PENDING_RECEIPT, sa_column=Column("status", type_=SQLEnum(TxStatus))
    )
    matched_invoice_id: str | None = Field(default=None, foreign_key="invoices.id")
    raw_text: str = ""
    note: str = ""
    category: str = ""
    location_lat: float | None = None
    location_lon: float | None = None
    location_captured_at: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    user: Mapped["User"] = Relationship(back_populates="transactions")
    attachments: Mapped[list["TransactionAttachment"]] = Relationship(back_populates="transaction")
    links: Mapped[list["InvoiceTransactionLink"]] = Relationship(
        back_populates="transaction",
        sa_relationship_kwargs={
            "foreign_keys": "InvoiceTransactionLink.transaction_id",
            "cascade": "all, delete-orphan",
        },
    )
    matched_invoice: Mapped[Optional["Invoice"]] = Relationship(sa_relationship_kwargs={"foreign_keys": "PaymentTransaction.matched_invoice_id"})


class TransactionAttachment(SQLModel, table=True):
    """Attachment for a transaction."""

    __tablename__ = "transaction_attachments"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    transaction_id: str = Field(foreign_key="payment_transactions.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    storage_key: str = ""
    mime_type: str = ""
    size: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    transaction: Mapped[Optional["PaymentTransaction"]] = Relationship(back_populates="attachments")


class SharedPlaceVisit(SQLModel, table=True):
    """Shared place visit for community suggestions."""

    __tablename__ = "shared_place_visits"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    place_name: str = ""
    place_kind: str = ""
    lat: float = 0.0
    lon: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserPaymentPreference(SQLModel, table=True):
    __tablename__ = "user_payment_preferences"

    user_id: str = Field(foreign_key="users.id", primary_key=True)
    share_place_visits: bool = False


class UserEmailAccount(SQLModel, table=True):
    __tablename__ = "user_email_accounts"

    user_id: str = Field(foreign_key="users.id", primary_key=True)
    host: str = ""
    port: int = 993
    username: str = ""
    encrypted_password: str = ""
    folder: str = "INBOX"
    interval_seconds: int = 60


class PushSubscription(SQLModel, table=True):
    """Web Push subscription."""

    __tablename__ = "push_subscriptions"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    endpoint: str = Field(unique=True)
    p256dh: str = ""
    auth: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = Relationship(back_populates="push_subscriptions")


class EmailVerification(SQLModel, table=True):
    """Email verification token."""

    __tablename__ = "email_verifications"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token: str = Field(unique=True, index=True)
    expires_at: str = ""
    used: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PasswordReset(SQLModel, table=True):
    """Password reset token."""

    __tablename__ = "password_resets"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token: str = Field(unique=True, index=True)
    expires_at: str = ""
    used: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RefreshToken(SQLModel, table=True):
    """Refresh token for JWT rotation."""

    __tablename__ = "refresh_tokens"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    jti: str = Field(unique=True, index=True)
    expires_at: str = ""
    revoked: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApiKey(SQLModel, table=True):
    """API key cho bên thứ ba — DB chỉ lưu hash; plaintext trả về 1 lần lúc tạo."""

    __tablename__ = "api_keys"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    name: str = ""
    key_hash: str = Field(unique=True, index=True)
    key_prefix: str = ""  # "sk_live_ab12…" — hiển thị trong danh sách, không dùng xác thực
    scopes: str = ""  # "invoices:read,payments:read"
    rate_limit_per_min: int = 60
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Pydantic Models for API (request/response) ---
class UserCreate(SQLModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=254)


class UserPublic(SQLModel):
    id: str
    username: str
    email: str = ""


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    verification_sent: bool = False


class InvoiceCreate(SQLModel):
    invoice_number: str = "unknown"
    vendor: str = "unknown"
    issue_date: str | None = None
    due_date: str | None = None
    currency: str = "USD"
    total: float = 0.0
    tax: float = 0.0
    status: InvoiceStatus = InvoiceStatus.UNPAID


class InvoiceUpdate(SQLModel):
    status: InvoiceStatus | None = None
    total: float | None = None
    vendor: str | None = None
    invoice_number: str | None = None
    issue_date: str | None = None
    due_date: str | None = None
    tax: float | None = None
    currency: str | None = None


class FieldCorrection(SQLModel):
    invoice_id: str = ""
    field: str
    old_value: Any | None = None
    new_value: Any = None
    confidence: float | None = None
    source: str | None = None
    corrected_by: str = ""
    corrected_at: str = ""


class MonthlyReport(SQLModel):
    period: str
    invoice_count: int = 0
    total_amount: float = 0.0
    total_tax: float = 0.0
    total_discount: float = 0.0
    paid_amount: float = 0.0
    unpaid_amount: float = 0.0
    paid_count: int = 0


# --- API Keys (bên thứ ba) ---
class ApiKeyCreate(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["invoices:read"])
    rate_limit_per_min: int = Field(default=60, ge=1, le=1000)
    expires_at: datetime | None = None


class ApiKeyPublic(SQLModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_per_min: int
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True
    created_at: datetime


class ApiKeyCreated(ApiKeyPublic):
    """Response khi tạo key — plaintext `key` chỉ trả về đúng 1 lần này."""

    key: str


# --- Payment Event (for parsing) ---
class PaymentEvent(SQLModel):
    amount: float
    currency: str = "VND"
    direction: TxDirection = TxDirection.DEBIT
    merchant: str = ""
    occurred_at: str | None = None
    source: str = "sms"


# --- Database helper ---
def create_db_and_tables(engine):
    SQLModel.metadata.create_all(engine)
