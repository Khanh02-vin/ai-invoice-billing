"""Mô hình miền cho Invoice & Billing. ponytail: giữ Pydantic để xác thực.

Nguyên tắc (PLAN mục 3):
- Schema versioned với line items, tax IDs, provenance.
- Mỗi field lưu value, confidence, source, page, bbox/text_span nếu có.
- Job model: PENDING, PROCESSING, REVIEW, FINALIZED, FAILED.
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# --- Job status cho workflow upload → review → finalize ---
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


# --- Field provenance: giá trị + confidence + nguồn + evidence ---
class FieldProvenance(BaseModel):
    """Provenance cho một field — giá trị, confidence, nguồn, evidence."""
    value: Any
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source: str = "unverified"  # regex | ocr | llm | manual | unverified
    page: Optional[int] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1]
    text_span: Optional[str] = None
    evidence: Optional[str] = None  # đoạn text gốc làm bằng chứng


class LineItem(BaseModel):
    """Một dòng hàng trong hóa đơn."""
    description: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0
    provenance: Optional[FieldProvenance] = None


class Invoice(BaseModel):
    """Một hóa đơn đã trích xuất và lưu trữ — schema versioned."""
    id: str = ""
    invoice_number: str = "unknown"
    vendor: str = "unknown"
    buyer: str = ""
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: str = "USD"
    subtotal: float = 0.0
    total: float = 0.0
    tax: float = 0.0
    tax_rate: float = 0.0
    discount: float = 0.0
    amount_due: float = 0.0
    status: InvoiceStatus = InvoiceStatus.UNPAID
    source_file: str = ""
    raw_snippet: str = ""
    confidence: float = 0.0
    user_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # --- Schema versioned mới ---
    schema_version: int = 2
    supplier_tax_id: str = ""
    customer_tax_id: str = ""
    line_items: List[LineItem] = Field(default_factory=list)
    provenance: Dict[str, FieldProvenance] = Field(default_factory=dict)
    job_status: JobStatus = JobStatus.REVIEW
    finalized_at: Optional[datetime] = None
    finalized_by: Optional[str] = None
    file_checksum: str = ""
    file_size: int = 0
    page_count: Optional[int] = None
    extraction_provider: str = ""  # regex | ocr | llm
    review_history: List[Dict[str, Any]] = Field(default_factory=list)


class User(BaseModel):
    """Người dùng."""
    id: str = ""
    username: str = ""
    password_hash: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    verified: bool = False
    mfa_enabled: bool = False


class UserCreate(BaseModel):
    """Dữ liệu đầu vào để đăng ký."""
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class UserPublic(BaseModel):
    """Thông tin người dùng công khai."""
    id: str
    username: str


class Token(BaseModel):
    """Access token plus optional opaque refresh token."""
    access_token: str
    token_type: str = "bearer"
    refresh_token: Optional[str] = None
    verification_token: Optional[str] = None


class InvoiceCreate(BaseModel):
    """Dữ liệu đầu vào để tạo hóa đơn."""
    invoice_number: str = "unknown"
    vendor: str = "unknown"
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: str = "USD"
    total: float = 0.0
    tax: float = 0.0
    status: InvoiceStatus = InvoiceStatus.UNPAID


class InvoiceUpdate(BaseModel):
    """Các trường có thể cập nhật."""
    status: Optional[InvoiceStatus] = None
    total: Optional[float] = None
    vendor: Optional[str] = None


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
