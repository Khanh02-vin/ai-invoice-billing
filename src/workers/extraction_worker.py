"""Extraction worker — background OCR + LLM extraction.

This worker processes invoice extraction asynchronously:
1. Receive invoice_id + storage_key from queue
2. Read file from storage
3. Run OCR/extraction (XML first for e-invoice, then general extractor)
4. Update invoice status in database
5. Auto-match against pending payment transactions (unique match only)
6. Handle failures with retry + dead letter queue

Architecture:
- Tasks are idempotent (check status before processing)
- Exponential backoff on transient failures
- Dead letter queue for permanent failures
"""

from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path

from ..config import get_settings
from ..domain.models import JobStatus
from ..extract.extractor import extract_invoice as extract_fn
from ..extract.xml_parser import parse_xml_invoice
from ..models import Invoice, InvoiceStatus, InvoiceUpdate, TxStatus
from ..payments.matching import pick_invoices_decision
from ..payments.service import match_invoice_saved
from ..store.sql_repository import (
    Database,
    InvoiceRepository,
    InvoiceTransactionLinkRepository,
    TransactionRepository,
)
from ..store.storage import LocalFileStorage, compute_fingerprint
from .celery_app import app

logger = logging.getLogger(__name__)


def _do_extract(
    invoice_id: str,
    storage_key: str,
    user_id: str = "",
    db_path: str | None = None,
    storage_dir: str | None = None,
    invoice_repo=None,
    tx_repo=None,
    transaction_id: str = "",
    organization_id: str | None = None,
    mark_failed_on_error: bool = True,
):
    """Extract invoice data from file asynchronously (core logic).

    Args:
        invoice_id: Database record ID
        storage_key: Storage key of the uploaded file
        user_id: Owner user ID
        db_path: Optional database path (defaults to settings)
        storage_dir: Optional storage dir (defaults to settings)
        invoice_repo: Optional pre-built InvoiceRepository (for tests)
        tx_repo: Optional pre-built TransactionRepository (for tests)
        transaction_id: Explicitly selected transaction (POST /upload?transaction_id=).
            Ưu tiên ghép với giao dịch này; không khớp số tiền thì không ghép gì.
        organization_id: Org context của upload (None = cá nhân) — dùng khi tra
            giao dịch tường minh; auto-match tự scope theo organization_id của
            bản ghi hóa đơn.
        mark_failed_on_error: Đánh dấu FAILED ngay khi lỗi. Celery task truyền False
            (FAILED chỉ đặt khi HẾT lượt retry, để các lần retry chạy lại được).
    """
    if invoice_repo is None:
        _db_path = db_path or get_settings().database_path
        invoice_repo = InvoiceRepository(Database(_db_path))
    if tx_repo is None:
        _db_path2 = db_path or get_settings().database_path
        tx_repo = TransactionRepository(Database(_db_path2))
    link_repo = InvoiceTransactionLinkRepository(tx_repo.db)
    _storage_dir = storage_dir or get_settings().storage_dir
    print(f"DEBUG _do_extract: storage_dir_param={storage_dir!r}, get_settings().storage_dir={get_settings().storage_dir!r}, storage_key={storage_key}")
    storage = LocalFileStorage(_storage_dir)

    existing = invoice_repo.get(invoice_id, user_id=user_id)
    if not existing:
        raise ValueError(f"Invoice {invoice_id} does not exist")
    if existing.job_status in (JobStatus.FINALIZED, JobStatus.FAILED):
        logger.info(f"Invoice {invoice_id} already processed, skipping")
        return {"status": "skipped", "reason": "already_processed"}

    # Mark as processing
    existing.job_status = JobStatus.PROCESSING
    invoice_repo.upsert(existing)

    try:
        start_time = time.time()

        # Read file from storage
        file_bytes = storage.get(storage_key)
        if file_bytes is None:
            raise ValueError(f"File {storage_key} not found in storage")

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(storage_key)
        mime_type = mime_type or "application/octet-stream"

        # Try XML first (e-invoice), then fall back to general extractor
        invoice = None
        if mime_type in ("application/xml", "text/xml"):
            invoice = parse_xml_invoice(file_bytes)
            if invoice:
                invoice.user_id = user_id
                invoice.source_file = storage_key
        if invoice is None:
            invoice = extract_fn(
                content=file_bytes,
                mime_type=mime_type,
                source_file=storage_key,
                user_id=user_id,
            )
        print(f"DEBUG: invoice.provenance = {invoice.provenance}")
        if not invoice or not invoice.invoice_number or invoice.total <= 0:
            raise ValueError("Could not extract valid invoice data")

        # Update existing invoice row with extracted data
        existing.invoice_number = invoice.invoice_number or existing.invoice_number
        existing.vendor = invoice.vendor or existing.vendor
        existing.buyer = invoice.buyer or existing.buyer
        existing.issue_date = invoice.issue_date or existing.issue_date
        existing.due_date = invoice.due_date or existing.due_date
        existing.currency = invoice.currency or existing.currency
        existing.subtotal = invoice.subtotal or existing.subtotal
        existing.total = invoice.total or existing.total
        existing.tax = invoice.tax or existing.tax
        existing.tax_rate = invoice.tax_rate or existing.tax_rate
        existing.discount = invoice.discount or existing.discount
        existing.amount_due = invoice.amount_due or existing.amount_due
        existing.job_status = JobStatus.REVIEW
        existing.file_checksum = compute_fingerprint(file_bytes)
        existing.file_size = len(file_bytes)

        # Convert provenance values to plain dict for JSON serialization
        prov = {}
        for k, v in (invoice.provenance or {}).items():
            prov[k] = v.model_dump() if hasattr(v, "model_dump") else dict(v)
        existing.provenance = prov

        saved = invoice_repo.upsert(existing)

        # Đối soát: transaction_id chọn tường minh có quyền ưu tiên (giữ hợp đồng
        # cũ của POST /upload?transaction_id= — khớp số tiền thì link, lệch thì
        # để giao dịch tiếp tục chờ). Không chọn thì tự ghép giao dịch pending
        # duy nhất; ambiguous/none -> để người dùng chọn thủ công.
        matched_tx = None
        if transaction_id:
            from ..payments.matching import amounts_match
            from ..payments.service import link

            # Org context: giao dịch tường minh có thể do member khác tạo —
            # tra theo organization_id thay vì user_id.
            tx = tx_repo.get(
                transaction_id,
                user_id="" if organization_id else user_id,
                organization_id=organization_id,
            )
            if (
                tx
                and getattr(tx.status, "value", tx.status) == TxStatus.PENDING_RECEIPT.value
                and amounts_match(tx.amount, tx.currency, saved.total, saved.currency)
            ):
                link(tx_repo, invoice_repo, tx, saved)
                matched_tx = tx
        else:
            matched_tx = match_invoice_saved(tx_repo, invoice_repo, saved)

        saved = invoice_repo.get(invoice_id, user_id=user_id)
        saved.job_status = JobStatus.FINALIZED if matched_tx else JobStatus.REVIEW
        invoice_repo.upsert(saved)

        # Web Push best-effort sau khi trích xuất xong (thiếu VAPID -> im lặng):
        # đã ghép -> báo matched; chưa -> nhắc vào review các trường máy đọc.
        try:
            from ..push import send_push_to_user

            amount = f"{saved.total:,.0f} {saved.currency}"
            if matched_tx:
                send_push_to_user(
                    user_id,
                    f"Hóa đơn {saved.invoice_number} đã được ghép tự động",
                    f"Đã đối soát với giao dịch {amount}. Mở app để xem chi tiết.",
                    url="/",
                )
            else:
                send_push_to_user(
                    user_id,
                    f"Hóa đơn {saved.invoice_number} cần bạn kiểm tra",
                    f"{saved.vendor}: {amount}. Kiểm tra các trường máy đọc rồi xác nhận.",
                    url="/",
                )
        except Exception:
            logger.exception("Push sau extraction thất bại cho %s", invoice_id)

        elapsed = time.time() - start_time
        logger.info(f"Extracted invoice {invoice_id} in {elapsed:.2f}s")
        return {"status": "success", "invoice_id": invoice_id, "elapsed_seconds": elapsed}

    except Exception as exc:
        logger.error(f"Extraction failed for {invoice_id}: {exc}")
        if mark_failed_on_error and existing:
            try:
                existing.job_status = JobStatus.FAILED
                invoice_repo.upsert(existing)
            except Exception:
                logger.exception("Không đánh dấu được FAILED cho %s", invoice_id)
        raise


def _set_job_status(invoice_id: str, status: JobStatus) -> None:
    """Cập nhật job_status bằng repo riêng (dùng trong wrapper task, sau khi lỗi)."""
    try:
        repo = InvoiceRepository(Database(get_settings().database_path))
        inv = repo.get(invoice_id)
        if inv:
            inv.job_status = status
            repo.upsert(inv)
    except Exception:
        logger.exception("Không cập nhật được job_status=%s cho %s", status, invoice_id)


@app.task(
    bind=True,
    name="src.workers.extraction_worker.extract_invoice",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    rate_limit="10/m",  # Max 10 extractions per minute
    priority=0,  # High priority
)
def extract_invoice(
    self,
    invoice_id: str,
    storage_key: str,
    user_id: str = "",
    db_path: str | None = None,
    storage_dir: str | None = None,
    invoice_repo=None,
    tx_repo=None,
    transaction_id: str = "",
    organization_id: str | None = None,
):
    """Celery task wrapper around _do_extract().

    Retry với backoff; invoice chỉ đánh dấu FAILED khi HẾT lượt retry (các lần
    retry giữa chừng giữ PROCESSING để chạy lại được — tránh bị skip vì FAILED).
    """
    try:
        return _do_extract(
            invoice_id, storage_key, user_id, db_path, storage_dir,
            invoice_repo, tx_repo,
            transaction_id=transaction_id,
            organization_id=organization_id,
            mark_failed_on_error=False,
        )
    except Exception as exc:
        if self.request.retries < (self.max_retries or 0):
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        _set_job_status(invoice_id, JobStatus.FAILED)
        raise


@app.task(
    name="src.workers.extraction_worker.extract_batch",
    bind=True,
    max_retries=1,
    acks_late=True,
    rate_limit="2/m",  # Max 2 batches per minute
    priority=5,  # Lower priority than single extractions
)
def extract_batch(self, batch_id: str, file_paths: list, user_id: str):
    """Process a batch of invoice files.

    Args:
        batch_id: Batch record ID
        file_paths: List of (filename, storage_key) tuples
        user_id: Owner user ID
    """
    _db_path = get_settings().database_path
    repo = InvoiceRepository(Database(_db_path))
    results = {"successful": 0, "failed": 0, "errors": []}

    for filename, storage_key in file_paths:
        try:
            # Create invoice record
            invoice = Invoice(
                user_id=user_id,
                source_file=filename,
                job_status=JobStatus.PENDING,
            )
            invoice = repo.upsert(invoice)

            # Enqueue extraction
            extract_invoice.delay(invoice.id, storage_key, user_id)
            results["successful"] += 1

        except Exception as exc:
            results["failed"] += 1
            results["errors"].append({"file": filename, "error": str(exc)})

    return results
