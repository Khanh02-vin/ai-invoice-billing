"""Upload endpoint — refactored for async processing.

Changes:
1. Idempotency check before processing
2. Background task via Celery (not synchronous)
3. Cost tracking for LLM usage
4. Proper error handling + retry logic

Flow:
1. Validate upload (size, mime, etc.)
2. Check idempotency key
3. Save file to storage
4. Create invoice record (status=PENDING)
5. Enqueue background extraction task
6. Return immediately with invoice ID
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from ..app import current_user
from ..domain.models import Invoice, User
from ..errors import AppError
from ..security.idempotency import (
    IdempotencyCache,
    InMemoryCache,
    compute_content_hash,
    generate_idempotency_key,
)
from ..store.repository import InvoiceRepository, DuplicateInvoiceError
from ..upload import validate_upload
from ..config import get_settings
from ..observability.metrics import inc_uploads_failed

router = APIRouter()
settings = get_settings()

# Cache backend (Redis in production, in-memory for dev/test)
_idempotency_cache: Optional[IdempotencyCache] = None


def get_idempotency_cache() -> IdempotencyCache:
    """Get or create idempotency cache singleton."""
    global _idempotency_cache
    if _idempotency_cache is None:
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            from ..security.idempotency import RedisCache
            _idempotency_cache = RedisCache(redis_url)
        else:
            _idempotency_cache = InMemoryCache()
    return _idempotency_cache


@router.post("/upload-async", response_model=dict)
async def upload_invoice_async(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    idempotency_key: Optional[str] = None,
):
    """Upload file for async extraction.

    Returns immediately with:
    - invoice_id: Use to track status
    - status: "pending" | "duplicate"
    - message: Human-readable status

    Client should poll GET /invoices/{id}/status or use SSE for updates.
    """
    from ..workers.extraction_worker import extract_invoice

    # 1. Read file content
    content = await file.read()
    if not content:
        raise AppError("EMPTY_FILE", "File is empty", status=400)

    # 2. Validate upload
    validated = validate_upload(
        content=content,
        filename=file.filename or "upload",
        declared_mime=file.content_type or "application/octet-stream",
        max_bytes=settings.max_upload_bytes,
        allowed_mimes=settings.allowed_mime_types,
        max_pdf_pages=settings.max_pdf_pages,
    )

    # 3. Check idempotency
    cache = get_idempotency_cache()
    content_hash = compute_content_hash(content)
    idem_key = idempotency_key or generate_idempotency_key(
        user_id=user.id,
        filename=validated.filename,
        file_size=len(content),
        content_hash=content_hash,
    )

    cached = cache.get(idem_key)
    if cached:
        return JSONResponse(
            content={
                "invoice_id": cached["invoice_id"],
                "status": "duplicate",
                "message": "Request already processed",
            },
            status_code=200,
            headers={"X-Idempotent-Replay": "true"},
        )

    # 4. Save file to storage
    from ..store.storage import LocalFileStorage, generate_storage_key, compute_fingerprint
    storage = LocalFileStorage(settings.storage_dir)
    storage_key = generate_storage_key(validated.filename, content)
    stored = storage.put(storage_key, content)
    file_path = stored.path

    # 5. Create invoice record (PENDING)
    # status phải là InvoiceStatus hợp lệ — "pending" chỉ có ở job_status
    repo = InvoiceRepository()
    invoice = Invoice(
        user_id=user.id,
        source_file=validated.filename,
        file_checksum=compute_fingerprint(content),
        file_size=len(content),
        job_status="pending",
    )
    try:
        saved = repo.upsert(invoice)
    except DuplicateInvoiceError as e:
        raise AppError(
            "DUPLICATE_INVOICE",
            f"Hóa đơn số {e.invoice_number} đã tồn tại.",
            status=409,
            details={"existing_id": e.existing_id},
        )

    # 6. Enqueue background extraction
    task = extract_invoice.delay(
        invoice_id=saved.id,
        file_path=file_path,
        user_id=user.id,
    )

    # 7. Store in idempotency cache
    cache.set(idem_key, {
        "invoice_id": saved.id,
        "task_id": task.id,
    })

    # 8. Return immediately
    return {
        "invoice_id": saved.id,
        "task_id": task.id,
        "status": "pending",
        "message": "Invoice queued for extraction",
        "poll_url": f"/invoices/{saved.id}/status",
    }


@router.get("/invoices/{invoice_id}/status")
async def get_extraction_status(
    invoice_id: str,
    user: User = Depends(current_user),
):
    """Get extraction status for an invoice.

    Returns:
    - status: "pending" | "processing" | "review" | "finalized" | "failed"
    - progress: Optional progress info
    - result: Invoice data if completed
    """
    repo = InvoiceRepository()
    invoice = repo.get(invoice_id)

    if not invoice:
        raise AppError("NOT_FOUND", "Invoice not found", status=404)

    if invoice.user_id != user.id:
        raise AppError("FORBIDDEN", "Access denied", status=403)

    return {
        "invoice_id": invoice.id,
        "status": invoice.job_status.value if invoice.job_status else "unknown",
        "result": invoice if invoice.job_status.value == "review" else None,
    }
