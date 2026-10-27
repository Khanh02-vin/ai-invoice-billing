"""Ghép giao dịch <-> hóa đơn theo số tiền + tiền tệ.

Chỉ so amount + currency (tolerance VND 1.000 do làm tròn, ngoại tệ 0.01).
Ưu tiên hóa đơn CHƯA thanh toán khi có nhiều ứng viên.

pick_invoices mở rộng sang trường hợp 1 giao dịch thanh toán GỘP nhiều hóa đơn
nhỏ: không khớp 1-1 thì tìm tập hợp hóa đơn chưa thanh toán có tổng bằng tiền
giao dịch (subset-sum, giới hạn 6 hóa đơn để không nổ thời gian chạy).

pick_invoices_decision phân biệt rõ ba trường hợp:
- "unique": đúng 1 ứng viên (hoặc đúng 1 tổ hợp) -> tự động ghép an toàn.
- "ambiguous": nhiều ứng viên ngang hàng -> KHÔNG tự ghép, để người dùng chọn.
- "none": không tìm được -> giữ giao dịch pending.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from ..models import Invoice, InvoiceStatus
from ..domain.payments import PaymentTransaction, TxDirection

# Số hóa đơn tối đa xét khi tìm tập hợp khớp giao dịch
_MAX_COMBINATION = 6


@dataclass(frozen=True)
class InvoiceMatchResult:
    """Kết quả quyết định ghép đối chiếu."""

    kind: str  # "unique" | "ambiguous" | "none"
    invoices: list[Invoice]
    reason: str = ""


def amounts_match(amount_a: float, currency_a: str, amount_b: float, currency_b: str) -> bool:
    cur_a = (currency_a or "").upper()
    if cur_a != (currency_b or "").upper() or amount_a <= 0 or amount_b <= 0:
        return False
    tolerance = 1000.0 if cur_a == "VND" else 0.01
    return abs(amount_a - amount_b) <= tolerance


def pick_invoice_candidates(
    tx: PaymentTransaction, invoices: Iterable[Invoice]
) -> list[Invoice]:
    """Tất cả hóa đơn chưa thanh toán khớp số tiền giao dịch (1-1)."""
    cands = [
        i
        for i in invoices
        if i.status not in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED)
        and amounts_match(tx.amount, tx.currency, i.total, i.currency)
    ]
    cands.sort(key=lambda i: i.status == InvoiceStatus.PAID)  # unpaid trước
    return cands


def pick_invoice(tx: PaymentTransaction, invoices: Iterable[Invoice]) -> Invoice | None:
    """Chọn hóa đơn khớp số tiền giao dịch — ưu tiên chưa thanh toán.

    Giữ lại cho backward compatibility. Nếu có nhiều ứng viên, trả về ứng viên
    đầu tiên sau khi sắp xếp — caller NÊN dùng pick_invoices_decision để tránh
    ghép nhầm khi ambiguous.
    """
    cands = pick_invoice_candidates(tx, invoices)
    return cands[0] if cands else None


def pick_transaction(
    invoice: Invoice, txs: Iterable[PaymentTransaction]
) -> PaymentTransaction | None:
    """Chọn giao dịch debit khớp số tiền hóa đơn (list trả về mới nhất trước)."""
    for tx in txs:
        if tx.direction == TxDirection.DEBIT and amounts_match(
            tx.amount, tx.currency, invoice.total, invoice.currency
        ):
            return tx
    return None


def pick_invoices(
    tx: PaymentTransaction, invoices: Iterable[Invoice]
) -> list[Invoice]:
    """Chọn (các) hóa đơn khớp giao dịch — 1-1 trước, sau đến tổ hợp.

    - Có hóa đơn khớp đúng số tiền → [hóa đơn đó] (luồng 1-1 cũ).
    - Không → thử tập hợp hóa đơn CHƯA thanh toán cùng tiền tệ có TỔNG bằng
      tiền giao dịch (chuyển khoản gộp nhiều đơn), tập nhỏ nhất trước.
    - Không tìm được → [] — caller giữ giao dịch pending hoặc báo rà soát.

    Giữ lại cho backward compatibility. Nếu có nhiều ứng viên ngang hàng, hàm
    này trả về ứng viên đầu tiên — caller NÊN dùng pick_invoices_decision để
    tránh ghép nhầm khi ambiguous.
    """
    invoices = list(invoices)
    single = pick_invoice(tx, invoices)
    if single is not None:
        return [single]

    currency = (tx.currency or "").upper()
    tolerance = 1000.0 if currency == "VND" else 0.01
    candidates = [
        i
        for i in invoices
        if i.status not in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED)
        and (i.currency or "").upper() == currency
        and 0 < i.total <= tx.amount + tolerance
    ]
    candidates.sort(key=lambda i: (i.status != InvoiceStatus.UNPAID, -i.total))
    if len(candidates) > _MAX_COMBINATION:
        candidates = candidates[:_MAX_COMBINATION]

    for size in range(2, len(candidates) + 1):
        for combo in combinations(candidates, size):
            if abs(sum(i.total for i in combo) - tx.amount) <= tolerance:
                return list(combo)
    return []


def pick_invoice_combinations(
    tx: PaymentTransaction, invoices: Iterable[Invoice]
) -> list[list[Invoice]]:
    """Tất cả tổ hợp hợp lệ (tổ hợp nhỏ nhất trước) khớp tổng tiền giao dịch.

    Chỉ xét khi không có exact single candidate nào.
    """
    currency = (tx.currency or "").upper()
    tolerance = 1000.0 if currency == "VND" else 0.01
    candidates = [
        i
        for i in invoices
        if i.status not in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED)
        and (i.currency or "").upper() == currency
        and 0 < i.total <= tx.amount + tolerance
    ]
    candidates.sort(key=lambda i: (i.status != InvoiceStatus.UNPAID, -i.total))
    if len(candidates) > _MAX_COMBINATION:
        candidates = candidates[:_MAX_COMBINATION]

    results: list[list[Invoice]] = []
    for size in range(2, len(candidates) + 1):
        for combo in combinations(candidates, size):
            if abs(sum(i.total for i in combo) - tx.amount) <= tolerance:
                results.append(list(combo))
        if results:
            # Chỉ giữ tổ hợp ở kích thước nhỏ nhất tìm được.
            return results
    return []


def pick_invoices_decision(
    tx: PaymentTransaction, invoices: Iterable[Invoice]
) -> InvoiceMatchResult:
    """Quyết định ghép đối chiếu an toàn.

    - Dù 1 exact single candidate -> unique.
    - Nhiều exact single candidates -> ambiguous (không ghép tự động).
    - Không có single -> tìm tổ hợp hợp lệ. Dù 1 tổ hợp -> unique.
      Nhiều tổ hợp -> ambiguous.
    - Không tìm được -> none.
    """
    invoices = list(invoices)
    singles = pick_invoice_candidates(tx, invoices)
    if len(singles) == 1:
        return InvoiceMatchResult("unique", singles, "exact_single")
    if len(singles) > 1:
        return InvoiceMatchResult("ambiguous", singles, "multiple_exact_singles")

    combos = pick_invoice_combinations(tx, invoices)
    if len(combos) == 1:
        return InvoiceMatchResult("unique", combos[0], "combination")
    if len(combos) > 1:
        return InvoiceMatchResult("ambiguous", combos[0], "multiple_combinations")
    return InvoiceMatchResult("none", [], "no_match")
