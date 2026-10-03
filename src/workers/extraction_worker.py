"""Extraction worker — background OCR + LLM extraction.

This worker processes invoice extraction asynchronously:
1. Receive invoice_id + file_path from queue
2. Run OCR/extraction
3. Update invoice status in database
4. Handle failures with retry + dead letter queue

Architecture:
- Tasks are idempotent (check status before processing)
- Exponential backoff on transient failures
- Dead letter queue for permanent failures
- Cost tracking for LLM usage
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from .celery_app import app

logger = logging.getLogger(__name__)


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
def extract_invoice(self, invoice_id: str, file_path: str, user_id: str = ""):
    """Extract invoice data from file.

    Args:
        invoice_id: Database record ID
        file_path: Path to uploaded file
        user_id: Owner user ID
    """
    from ..config import get_settings
    from ..store.repository import InvoiceRepository
    from ..store.transactions import TransactionRepository
    from ..extract.extractor import extract_invoice as extract_fn
    from ..observability.cost_tracker import cost_tracker

    # Cùng DATABASE_PATH với app — 2 repo trỏ 1 file DB
    _db_path = get_settings().database_path
    repo = InvoiceRepository(_db_path)
    tx_repo = TransactionRepository(_db_path)

    # Idempotency check — skip if already processed
    existing = repo.get(invoice_id)
    if not existing:
        raise ValueError(f"Invoice {invoice_id} does not exist")
    if existing.job_status.value in ("finalized", "review"):
        logger.info(f"Invoice {invoice_id} already processed, skipping")
        return {"status": "skipped", "reason": "already_processed"}

    # Mark as processing by persisting the current record.
    existing.job_status = "processing"
    repo.upsert(existing)

    try:
        start_time = time.time()

        # Run extraction
        file_bytes = Path(file_path).read_bytes()
        invoice = extract_fn(
            content=file_bytes,
            mime_type=_guess_mime(file_path),
            source_file=file_path,
            user_id=user_id,
        )

        # Update invoice with extracted data
        invoice.id = invoice_id
        invoice.user_id = user_id
        repo.upsert(invoice)
        elapsed = time.time() - start_time
        logger.info(f"Extracted invoice {invoice_id} in {elapsed:.2f}s")

        # Ghép với giao dịch ngân hàng/ví đang chờ nếu số tiền khớp
        try:
            from ..payments.service import match_invoice_saved
            match_invoice_saved(tx_repo, repo, invoice)
        except Exception as exc:  # match fail không làm hỏng kết quả extract
            logger.warning(f"Transaction match skipped for {invoice_id}: {exc}")

        # Track cost if LLM was used
        if hasattr(invoice, "extraction_cost_usd"):
            cost_tracker.record(
                user_id=user_id,
                operation="llm_extraction",
                tokens=getattr(invoice, "llm_tokens_used", 0),
                cost_usd=invoice.extraction_cost_usd,
            )

        return {
            "status": "success",
            "invoice_id": invoice_id,
            "elapsed_seconds": elapsed,
        }

    except Exception as exc:
        logger.error(f"Extraction failed for {invoice_id}: {exc}")

        # Update status to failed
        if existing:
            existing.job_status = "failed"
            repo.upsert(existing)

        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


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
        file_paths: List of (filename, file_path) tuples
        user_id: Owner user ID
    """
    from ..store.repository import InvoiceRepository

    repo = InvoiceRepository()
    results = {"successful": 0, "failed": 0, "errors": []}

    for filename, file_path in file_paths:
        try:
            # Create invoice record
            from ..domain.models import Invoice
            invoice = Invoice(user_id=user_id, source_file=filename, job_status="pending")
            invoice = repo.upsert(invoice)

            # Enqueue extraction
            extract_invoice.delay(invoice.id, file_path, user_id)
            results["successful"] += 1

        except Exception as exc:
            results["failed"] += 1
            results["errors"].append({"file": filename, "error": str(exc)})

    return results


def _guess_mime(file_path: str) -> str:
    """Guess MIME type from file extension."""
    ext = Path(file_path).suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt": "text/plain",
    }
    return mime_map.get(ext, "application/octet-stream")
