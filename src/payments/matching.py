"""Ghép giao dịch <-> hóa đơn theo số tiền + tiền tệ.

Chỉ so amount + currency (tolerance VND 1.000 do làm tròn, ngoại tệ 0.01).
Ưu tiên hóa đơn CHƯA thanh toán khi có nhiều ứng viên.
"""
from typing import Iterable, Optional

from ..domain.models import Invoice, InvoiceStatus
from ..domain.payments import PaymentTransaction, TxDirection


def amounts_match(amount_a: float, currency_a: str, amount_b: float, currency_b: str) -> bool:
    cur_a = (currency_a or "").upper()
    if cur_a != (currency_b or "").upper() or amount_a <= 0 or amount_b <= 0:
        return False
    tolerance = 1000.0 if cur_a == "VND" else 0.01
    return abs(amount_a - amount_b) <= tolerance


def pick_invoice(tx: PaymentTransaction, invoices: Iterable[Invoice]) -> Optional[Invoice]:
    """Chọn hóa đơn khớp số tiền giao dịch — ưu tiên chưa thanh toán."""
    cands = [
        i for i in invoices
        if i.status != InvoiceStatus.CANCELLED
        and amounts_match(tx.amount, tx.currency, i.total, i.currency)
    ]
    if not cands:
        return None
    cands.sort(key=lambda i: i.status == InvoiceStatus.PAID)  # unpaid trước
    return cands[0]


def pick_transaction(invoice: Invoice, txs: Iterable[PaymentTransaction]) -> Optional[PaymentTransaction]:
    """Chọn giao dịch debit khớp số tiền hóa đơn (list trả về mới nhất trước)."""
    for tx in txs:
        if tx.direction == TxDirection.DEBIT and amounts_match(
            tx.amount, tx.currency, invoice.total, invoice.currency
        ):
            return tx
    return None
