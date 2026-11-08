"""SQLModel models for Invoice & Billing System.

Migrated from Pydantic models to SQLModel for type-safe database operations.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from .domain.models import InvoiceStatus, JobStatus, TxDirection, TxStatus


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
    # Multi-tenant: NULL = dữ liệu cá nhân; set = thuộc org (xem docs/multi-tenant-spec.md)
    organization_id: str | None = Field(default=None, foreign_key="organizations.id", index=True)

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
    provenance: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    job_status: JobStatus = Field(
        default=JobStatus.REVIEW, sa_column=Column("job_status", type_=SQLEnum(JobStatus))
    )
    finalized_at: datetime | None = None
    finalized_by: str | None = None
    file_checksum: str = ""
    file_size: int = 0
    mime_type: str = ""
    page_count: int | None = None
    extraction_provider: str = ""
    review_history: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # Location/route fields (legacy, deprecated — xem ADR-0002)
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = None
    location_address: str | None = None
    transport_mode: str | None = None
    route_sequence: int | None = None

    # Payment location (nơi thực hiện thanh toán)
    payment_lat: float | None = None
    payment_lon: float | None = None
    payment_captured_at: str | None = None
    payment_accuracy_m: float | None = None
    payment_source: str | None = None  # gps | network | fused | manual
    payment_provider: str | None = None

    # Merchant location (vị trí cửa hàng)
    merchant_poi_id: str | None = None
    merchant_name: str | None = None
    merchant_address: str | None = None
    merchant_lat: float | None = None
    merchant_lon: float | None = None
    merchant_source: str | None = None  # poi_match | user_input | receipt_address | osm | google
    merchant_confidence: float | None = None
    merchant_verified_by_user: bool = False

    # Purchase context
    purchase_context_type: str | None = None  # physical | online | remote
    evidence_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    user_confirmed_location: bool = False

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
    # Multi-tenant: NULL = cá nhân; set = giao dịch của org
    organization_id: str | None = Field(default=None, foreign_key="organizations.id", index=True)
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
    # Legacy (deprecated)
    location_lat: float | None = None
    location_lon: float | None = None
    location_captured_at: str | None = None

    # Payment location
    payment_lat: float | None = None
    payment_lon: float | None = None
    payment_captured_at: str | None = None
    payment_accuracy_m: float | None = None
    payment_source: str | None = None
    payment_provider: str | None = None

    # Merchant location
    merchant_poi_id: str | None = None
    merchant_name: str | None = None
    merchant_address: str | None = None
    merchant_lat: float | None = None
    merchant_lon: float | None = None
    merchant_source: str | None = None
    merchant_confidence: float | None = None
    merchant_verified_by_user: bool = False

    # Purchase context
    purchase_context_type: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    user_confirmed_location: bool = False

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


class Purchase(SQLModel, table=True):
    """Manual purchase without invoice — captured from photo + user input."""

    __tablename__ = "purchases"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    organization_id: str | None = Field(default=None, foreign_key="organizations.id", index=True)

    item: str = ""
    category: str = ""
    merchant: str = ""
    amount: float = 0.0
    currency: str = "VND"
    occurred_at: str | None = None
    notes: str = ""
    source_file: str = ""
    file_checksum: str = ""
    file_size: int = 0
    mime_type: str = ""

    # Legacy (deprecated)
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = None
    location_address: str | None = None
    location_captured_at: str | None = None

    # Payment location
    payment_lat: float | None = None
    payment_lon: float | None = None
    payment_captured_at: str | None = None
    payment_accuracy_m: float | None = None
    payment_source: str | None = None
    payment_provider: str | None = None

    # Merchant location
    merchant_poi_id: str | None = None
    merchant_name: str | None = None
    merchant_address: str | None = None
    merchant_lat: float | None = None
    merchant_lon: float | None = None
    merchant_source: str | None = None
    merchant_confidence: float | None = None
    merchant_verified_by_user: bool = False

    # Purchase context
    purchase_context_type: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    user_confirmed_location: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PurchaseLabelSample(SQLModel, table=True):
    """Đặc trưng ảnh của một purchase đã xác nhận — để nhận diện lại món đã từng mua.

    Nhãn (item/category/merchant) là của người dùng nhập/sửa khi lưu, nên mỗi
    dòng là một mẫu dạy máy đúng chuẩn; ảnh mới trùng dHash hoặc chữ OCR là
    điền lại được nhãn cũ."""

    __tablename__ = "purchase_label_samples"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    purchase_id: str = Field(foreign_key="purchases.id", index=True)

    item: str = ""
    category: str = ""
    merchant: str = ""
    ocr_text: str = ""
    image_hash: int | None = None  # dHash 64-bit
    embedding: bytes | None = None  # chỗ cắm vector MobileNet (phase sau)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


class OAuthAccount(SQLModel, table=True):
    """External identity linked to a local user."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        # One provider identity can belong to only one local user.
        UniqueConstraint("provider", "provider_account_id"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    provider: str = Field(index=True)
    provider_account_id: str = Field(index=True)
    email: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GmailConnection(SQLModel, table=True):
    """A user's Gmail connection used for payment-alert ingestion."""

    __tablename__ = "gmail_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "gmail_account_id"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    gmail_account_id: str = Field(index=True)
    email: str = Field(default="", index=True)
    encrypted_refresh_token: str = ""
    history_id: str | None = None
    watch_expiration: datetime | None = None
    watch_topic: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GmailPaymentAlert(SQLModel, table=True):
    """A deduplicated payment alert received from a Gmail connection."""

    __tablename__ = "gmail_payment_alerts"
    __table_args__ = (
        UniqueConstraint("connection_id", "gmail_message_id"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    connection_id: str = Field(foreign_key="gmail_connections.id", index=True)
    gmail_message_id: str = Field(index=True)
    thread_id: str = ""
    sender: str = ""
    subject: str = ""
    received_at: datetime | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


# --- Multi-tenant Organization ---
ORG_ROLES = ("owner", "admin", "member", "viewer")


class Organization(SQLModel, table=True):
    """Tổ chức — nhiều user dùng chung kho hóa đơn/giao dịch."""

    __tablename__ = "organizations"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(unique=True)
    owner_user_id: str = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrganizationMember(SQLModel, table=True):
    """Thành viên org + role (owner|admin|member|viewer)."""

    __tablename__ = "organization_members"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], primary_key=True)
    organization_id: str = Field(foreign_key="organizations.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    role: str = "member"
    joined_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    location_lat: float | None = None
    location_lon: float | None = None
    location_name: str | None = None
    location_address: str | None = None
    payment_lat: float | None = None
    payment_lon: float | None = None
    payment_captured_at: str | None = None
    payment_accuracy_m: float | None = None
    payment_source: str | None = None
    payment_provider: str | None = None
    merchant_poi_id: str | None = None
    merchant_name: str | None = None
    merchant_address: str | None = None
    merchant_lat: float | None = None
    merchant_lon: float | None = None
    merchant_source: str | None = None
    merchant_confidence: float | None = None
    merchant_verified_by_user: bool | None = None
    purchase_context_type: str | None = None
    evidence_refs: list[str] | None = None
    user_confirmed_location: bool | None = None


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


# --- Organization (multi-tenant) ---
class OrganizationCreate(SQLModel):
    name: str = Field(min_length=1, max_length=200)


class OrganizationPublic(SQLModel):
    id: str
    name: str
    slug: str
    role: str  # role của user hiện tại trong org
    created_at: datetime


class MemberAdd(SQLModel):
    username: str = Field(min_length=3, max_length=64)
    role: str = "member"  # admin|member|viewer (owner chỉ qua tạo org)


class MemberRoleUpdate(SQLModel):
    role: str


class MemberPublic(SQLModel):
    user_id: str
    username: str
    role: str
    joined_at: datetime


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
