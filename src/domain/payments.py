"""Mô hình miền cho giao dịch thanh toán (bank SMS/email, ví MoMo/ZaloPay...).

Luồng: text thô thông báo giao dịch -> parse -> PaymentTransaction (pending)
-> tự ghép hóa đơn theo số tiền -> nhắc user chụp hóa đơn nếu chưa có.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TxDirection(str, Enum):
    DEBIT = "debit"    # tiền đi ra -> cần hóa đơn
    CREDIT = "credit"  # tiền vào -> không cần hóa đơn


class TxStatus(str, Enum):
    PENDING_RECEIPT = "pending_receipt"  # chờ upload hóa đơn
    MATCHED = "matched"                  # đã ghép với hóa đơn
    DISMISSED = "dismissed"              # user bỏ qua


class PaymentEvent(BaseModel):
    """Kết quả parse 1 tin nhắn/email thông báo giao dịch."""
    amount: float
    currency: str = "VND"
    direction: TxDirection = TxDirection.DEBIT
    merchant: str = ""
    occurred_at: Optional[str] = None  # ISO datetime nếu đọc được ngày
    source: str = "sms"


class PaymentTransaction(BaseModel):
    """Giao dịch đã lưu — chờ ghép với hóa đơn."""
    id: str = ""
    user_id: str = ""
    amount: float = 0.0
    currency: str = "VND"
    direction: TxDirection = TxDirection.DEBIT
    merchant: str = ""
    occurred_at: Optional[str] = None
    source: str = "sms"
    external_ref: str = ""
    status: TxStatus = TxStatus.PENDING_RECEIPT
    matched_invoice_id: str = ""
    raw_text: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
