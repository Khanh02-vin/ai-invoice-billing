"""Pydantic models for API requests/responses + shared enums.

These are separate from SQLModel database models in src.models.
Enums are defined here as the source of truth; src.models imports them.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# --- Shared Enums (source of truth for both API and DB) ---


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


# --- Field Provenance (Pydantic for API) ---
class FieldProvenance(BaseModel):
    """Provenance for a field — value, confidence, source, evidence."""

    value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "unverified"
    page: int | None = None
    bbox: list[float] | None = None
    text_span: str | None = None
    evidence: str | None = None


class LineItem(BaseModel):
    """A line item in an invoice."""

    description: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0
    provenance: FieldProvenance | None = None


# --- API Request/Response Models ---


class UserCreate(BaseModel):
    """Dữ liệu đầu vào để đăng ký."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=254)


class UserPublic(BaseModel):
    """Thông tin người dùng công khai."""

    id: str
    username: str
    email: str = ""


class Token(BaseModel):
    """Access token plus optional opaque refresh token."""

    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    verification_sent: bool = False


class InvoiceCreate(BaseModel):
    """Dữ liệu đầu vào để tạo hóa đơn."""

    invoice_number: str = "unknown"
    vendor: str = "unknown"
    issue_date: str | None = None
    due_date: str | None = None
    currency: str = "USD"
    total: float = 0.0
    tax: float = 0.0
    status: InvoiceStatus = InvoiceStatus.UNPAID


class InvoiceUpdate(BaseModel):
    """Các trường có thể cập nhật — gồm cả field user sửa trong màn review."""

    status: InvoiceStatus | None = None
    total: float | None = None
    vendor: str | None = None
    invoice_number: str | None = None
    issue_date: str | None = None
    due_date: str | None = None
    tax: float | None = None
    currency: str | None = None


class FieldCorrection(BaseModel):
    """1 chỉnh sửa field do user sửa ở màn review — audit + data eval cho benchmark.

    old_value = extractor đọc ra, new_value = user sửa (coi như ground truth),
    confidence/source = provenance lúc máy đọc → biết ngưỡng nào là đáng ngờ.
    """

    invoice_id: str = ""
    field: str
    old_value: Any | None = None
    new_value: Any = None
    confidence: float | None = None
    source: str | None = None
    corrected_by: str = ""
    corrected_at: str = ""


class MonthlyReport(BaseModel):
    """Báo cáo theo tháng."""

    period: str  # YYYY-MM
    invoice_count: int = 0
    total_amount: float = 0.0
    total_tax: float = 0.0
    total_discount: float = 0.0
    paid_amount: float = 0.0
    unpaid_amount: float = 0.0
    paid_count: int = 0


# --- Payment Event (for parsing) ---
class PaymentEvent(BaseModel):
    amount: float
    currency: str = "VND"
    direction: TxDirection = TxDirection.DEBIT
    merchant: str = ""
    occurred_at: str | None = None
    source: str = "sms"


__all__ = [
    "FieldCorrection",
    "FieldProvenance",
    "InvoiceCreate",
    "InvoiceStatus",
    "InvoiceUpdate",
    "JobStatus",
    "LineItem",
    "MonthlyReport",
    "PaymentEvent",
    "Token",
    "TxDirection",
    "TxStatus",
    "UserCreate",
    "UserPublic",
]
