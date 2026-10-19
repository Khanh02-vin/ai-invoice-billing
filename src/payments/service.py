"""Orchestration: ingest giao dịch -> lưu -> tự ghép hóa đơn (2 chiều).

- Chiều A: giao dịch đến TRƯỚC -> tìm hóa đơn khớp số tiền -> link + đánh dấu paid.
- Chiều B: hóa đơn được lưu TRƯỚC -> tìm các giao dịch pending và ghép giao dịch có tổ hợp hóa đơn khớp.

Link = transaction matched_invoice_id + hóa đơn chuyển sang PAID
(nhắc user chụp bill chỉ xẩy ra khi KHÔNG ghép được).
"""

from ..models import Invoice, InvoiceStatus, InvoiceUpdate, TxStatus
from ..domain.payments import PaymentEvent, PaymentTransaction
from ..store.sql_repository import InvoiceTransactionLinkRepository
from .matching import pick_invoices
from .parser import derive_ref


def link(tx_repo, invoice_repo, tx: PaymentTransaction, invoice: Invoice, link_repo=None) -> None:
    """Gắn transaction với hóa đơn + đánh dấu hóa đơn đã thanh toán."""
    tx_repo.set_status(tx.id, TxStatus.MATCHED, invoice_id=invoice.id, user_id=tx.user_id)
    tx.status = TxStatus.MATCHED
    tx.matched_invoice_id = invoice.id
    if link_repo is None:
        link_repo = InvoiceTransactionLinkRepository(tx_repo.db)
    link_repo.create(tx.user_id, tx.id, invoice.id, min(tx.amount, invoice.total), "exact")
    if invoice.status != InvoiceStatus.PAID and invoice.status != InvoiceStatus.CANCELLED:
        invoice_repo.update(
            invoice.id, InvoiceUpdate(status=InvoiceStatus.PAID), user_id=invoice.user_id
        )
        invoice.status = InvoiceStatus.PAID


def link_many(tx_repo, invoice_repo, tx: PaymentTransaction, invoices: list[Invoice]) -> None:
    """Link a combined payment to each matched invoice."""
    if not invoices:
        return
    tx_repo.set_status(tx.id, TxStatus.MATCHED, invoice_id=invoices[0].id, user_id=tx.user_id)
    tx.status = TxStatus.MATCHED
    tx.matched_invoice_id = invoices[0].id
    links = InvoiceTransactionLinkRepository(tx_repo.db)
    for invoice in invoices:
        links.create(tx.user_id, tx.id, invoice.id, invoice.total, "combination")
        if invoice.status not in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED):
            invoice_repo.update(invoice.id, InvoiceUpdate(status=InvoiceStatus.PAID), user_id=invoice.user_id)
            invoice.status = InvoiceStatus.PAID


def ingest(
    tx_repo, invoice_repo, user_id: str, event: PaymentEvent, raw_text: str, external_ref: str = ""
) -> tuple[PaymentTransaction, Invoice | None, bool]:
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
        status=TxStatus.RECORDED if event.direction.value == "credit" else TxStatus.PENDING_RECEIPT,
    )
    tx = tx_repo.create(tx)
    if event.direction.value == "credit":
        return tx, None, False
    invoices = pick_invoices(tx, invoice_repo.list(user_id=user_id, limit=200))
    if len(invoices) > 1:
        link_many(tx_repo, invoice_repo, tx, invoices)
        return tx, invoices[0], False
    invoice = invoices[0] if invoices else None
    if invoice:
        link(tx_repo, invoice_repo, tx, invoice)
    return tx, invoice, False


def match_invoice_saved(tx_repo, invoice_repo, invoice: Invoice) -> PaymentTransaction | None:
    """Gọi sau khi lưu hóa đơn — ghép với giao dịch đang chờ nếu số tiền khớp."""
    if not invoice.user_id or invoice.total <= 0:
        return None
    pending = tx_repo.list(
        user_id=invoice.user_id, status=TxStatus.PENDING_RECEIPT, direction=None, limit=50
    )
    for tx in pending:
        matched = pick_invoices(tx, invoice_repo.list(user_id=invoice.user_id, limit=200))
        if invoice.id not in {item.id for item in matched}:
            continue
        if len(matched) > 1:
            link_many(tx_repo, invoice_repo, tx, matched)
        else:
            link(tx_repo, invoice_repo, tx, invoice)
        return tx
    return None
