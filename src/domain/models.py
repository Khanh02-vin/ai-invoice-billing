"""Mô hình miền cho Invoice & Billing. ponytail: giữ Pydantic để xác thực."""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class InvoiceStatus(str, Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class Invoice(BaseModel):
    """Một hóa đơn đã trích xuất và lưu trữ."""
    id: str
    invoice_number: str = "unknown"
    vendor: str = "unknown"
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: str = "USD"
    total: float = 0.0
    tax: float = 0.0
    discount: float = 0.0
    status: InvoiceStatus = InvoiceStatus.UNPAID
    source_file: str = ""
    raw_snippet: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    user_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class User(BaseModel):
    """Người dùng hệ thống."""
    id: str
    username: str
    password_hash: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class UserPublic(BaseModel):
    """Thông tin user trả về cho client — không bao giờ gửi password_hash."""
    id: str
    username: str
    created_at: datetime = Field(default_factory=datetime.now)


class UserCreate(BaseModel):
    """Đăng ký người dùng mới."""
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)


class Token(BaseModel):
    """Token JWT trả về khi đăng nhập."""
    access_token: str
    token_type: str = "bearer"


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
    unpaid_count: int = 0