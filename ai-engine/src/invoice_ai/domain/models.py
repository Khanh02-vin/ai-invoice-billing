"""Portable invoice extraction schemas, independent from billing persistence."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


class FieldProvenance(BaseModel):
    value: Any
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "unverified"
    page: Optional[int] = None
    bbox: Optional[List[float]] = None
    text_span: Optional[str] = None
    evidence: Optional[str] = None


class LineItem(BaseModel):
    description: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0
    provenance: Optional[FieldProvenance] = None


class Invoice(BaseModel):
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
