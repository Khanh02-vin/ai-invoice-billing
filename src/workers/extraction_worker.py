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
from ..models import Invoice, InvoiceStatus, InvoiceUpdate
from ..payments.matching import pick_invoices_decision
from ..payments.service import match_invoice_saved
from ..store.sql_repository import (
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
    """
    if invoice_repo is None:
        _db_path = db_path or get_settings().database_path
        invoice_repo = InvoiceRepository(_db_path)
    if tx_repo is None:
        _db_path2 = db_path or get_settings().database_path
        tx_repo = TransactionRepository(_db_path2)
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

        # Auto-match against pending payment transactions (unique match only;
        # ambiguous cases are left pending for user review)
        saved = invoice_repo.get(invoice_id, user_id=user_id)
        matched_tx = match_invoice_saved(tx_repo, invoice_repo, saved)
        if matched_tx:
            saved = invoice_repo.get(invoice_id, user_id=user_id)
            saved.job_status = JobStatus.FINALIZED
            saved.status = InvoiceStatus.PAID
            invoice_repo.upsert(saved)
        else:
            saved = invoice_repo.get(invoice_id, user_id=user_id)
            saved.job_status = JobStatus.REVIEW
            invoice_repo.upsert(saved)

        elapsed = time.time() - start_time
        logger.info(f"Extracted invoice {invoice_id} in {elapsed:.2f}s")
        return {"status": "success", "invoice_id": invoice_id, "elapsed_seconds": elapsed}

    except Exception as exc:
        logger.error(f"Extraction failed for {invoice_id}: {exc}")
        if existing:
            existing.job_status = JobStatus.FAILED
            invoice_repo.upsert(existing)
        raise


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
):
    """Celery task wrapper around _do_extract()."""
    try:
        return _do_extract(invoice_id, storage_key, user_id, db_path, storage_dir, invoice_repo, tx_repo)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))


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
    repo = InvoiceRepository(_db_path)
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
