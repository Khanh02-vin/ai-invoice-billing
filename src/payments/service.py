"""Orchestration: ingest giao dịch -> lưu -> tự ghép hóa đơn (2 chiều).

- Chiều A: giao dịch đến TRƯỚC -> tìm hóa đơn khớp số tiền -> link + đánh dấu paid.
- Chiều B: hóa đơn được lưu TRƯỚC -> tìm giao dịch pending khớp -> link.

Link = transaction matched_invoice_id + hóa đơn chuyển sang PAID
(nhắc user chụp bill chỉ xẩy ra khi KHÔNG ghép được).
"""
from typing import Optional, Tuple

from ..domain.models import Invoice, InvoiceStatus, InvoiceUpdate
from ..domain.payments import PaymentEvent, PaymentTransaction, TxStatus
from .matching import pick_invoice, pick_transaction
from .parser import derive_ref


def link(tx_repo, invoice_repo, tx: PaymentTransaction, invoice: Invoice) -> None:
    """Gắn transaction với hóa đơn + đánh dấu hóa đơn đã thanh toán."""
    tx_repo.set_status(tx.id, TxStatus.MATCHED, invoice_id=invoice.id, user_id=tx.user_id)
    tx.status = TxStatus.MATCHED
    tx.matched_invoice_id = invoice.id
    if invoice.status != InvoiceStatus.PAID and invoice.status != InvoiceStatus.CANCELLED:
        invoice_repo.update(
            invoice.id, InvoiceUpdate(status=InvoiceStatus.PAID), user_id=invoice.user_id
        )
        invoice.status = InvoiceStatus.PAID


def ingest(
    tx_repo,
    invoice_repo,
    user_id: str,
    event: PaymentEvent,
    raw_text: str,
    external_ref: str = "",
) -> Tuple[PaymentTransaction, Optional[Invoice], bool]:
    """Lưu 1 giao dịch và tự ghép hóa đơn.

    Trả về (transaction, invoice_ghep_neu_co, la_duplicate).
    """
    ref = derive_ref(raw_text, external_ref)
    existing = tx_repo.get_by_ref(user_id, ref)
    if existing:
        inv = (
            invoice_repo.get(existing.matched_invoice_id, user_id=user_id)
            if existing.matched_invoice_id
            else None
        )
        return existing, inv, True

    tx = PaymentTransaction(
        user_id=user_id,
        amount=event.amount,
        currency=event.currency,
        direction=event.direction,
        merchant=event.merchant,
        occurred_at=event.occurred_at,
        source=event.source,
        external_ref=ref,
        raw_text=raw_text,
        status=TxStatus.PENDING_RECEIPT,
    )
    tx = tx_repo.create(tx)
    invoice = pick_invoice(tx, invoice_repo.list(user_id=user_id, limit=200))
    if invoice:
        link(tx_repo, invoice_repo, tx, invoice)
    return tx, invoice, False


def match_invoice_saved(tx_repo, invoice_repo, invoice: Invoice) -> Optional[PaymentTransaction]:
    """Gọi sau khi lưu hóa đơn — ghép với giao dịch đang chờ nếu số tiền khớp."""
    if not invoice.user_id or invoice.total <= 0:
        return None
    pending = tx_repo.list(
        user_id=invoice.user_id, status=TxStatus.PENDING_RECEIPT, direction=None, limit=50
    )
    tx = pick_transaction(invoice, pending)
    if tx:
        link(tx_repo, invoice_repo, tx, invoice)
        return tx
    return None
