"""Payments router — ingest giao dịch ngân hàng/ví -> nhắc upload hóa đơn.

Routes:
- POST /payments/ingest                      -> parse text thô, lưu, tự ghép hóa đơn
- GET  /payments/transactions                -> danh sách (filter status=pending_receipt = notification feed)
- POST /payments/transactions/{id}/dismiss   -> bỏ qua nhắc nhở
- POST /payments/transactions/{id}/match     -> tự ghép tay với hóa đơn

Nguồn: SMS/email bank (VCB/ACB/TCB/MB...), ví MoMo/ZaloPay/VNPay — text thô
từ nguồn nào cũng qua chung parser (src/payments/parser.py).

Dùng current_user_local (copy pattern từ billing.py) để tránh import cycle với app.py.
"""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, Body, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from ..auth.security import decode_token
from ..domain.models import User
from ..domain.payments import PaymentTransaction, TxDirection, TxStatus
from ..errors import AppError
from ..payments.notify import send_receipt_reminder
from ..payments.parser import parse_payment_text
from ..payments.service import ingest as service_ingest, link
from ..store.users import UserRepository

router = APIRouter(prefix="/payments", tags=["payments"])

bearer = HTTPBearer(auto_error=False)

#懒: resolve qua app module khi request time (billing.py pattern) để test patch được
user_repo: UserRepository | None = None
tx_repo = None  # TransactionRepository
invoice_repo = None  # InvoiceRepository


def _canonical(module_attr: str, fallback_import: str):
    repo = globals()[module_attr]
    if repo is not None:
        return repo
    from .. import app as app_module
    return getattr(app_module, fallback_import)


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    """Local copy của current_user để tránh import cycle với app.py."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    repo = _canonical("user_repo", "users")
    user = repo.get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


class IngestRequest(BaseModel):
    raw_text: str = Field(min_length=4, max_length=4000)
    source: str = Field(default="sms", max_length=32)
    external_ref: str = Field(default="", max_length=128)


class MatchRequest(BaseModel):
    invoice_id: str


@router.post("/ingest")
def ingest_event(body: IngestRequest, user: User = Depends(current_user_local)) -> dict:
    """Nhận text thô thông báo giao dịch (SMS/email/wallet push) -> lưu + tự ghép.

    - Đọc được số tiền giao dịch: tạo transaction (pending nếu là debit).
    - Giao dịch credit (tiền vào): không lưu — không cần hóa đơn.
    - Trùng nội dung (retry/forward lại): trả về transaction đã có.
    - Không đọc được số tiền: 422 UNPARSEABLE.
    """
    event = parse_payment_text(body.raw_text, source=body.source)
    if event is None:
        raise AppError(
            "UNPARSEABLE",
            "Không đọc được số tiền giao dịch trong nội dung.",
            status=422,
        )
    if event.direction == TxDirection.CREDIT:
        return {"stored": False, "reason": "credit_not_tracked", "event": event.model_dump()}

    tx, invoice, duplicate = service_ingest(
        _canonical("tx_repo", "tx_repo"),
        _canonical("invoice_repo", "repo"),
        user.id,
        event,
        body.raw_text,
        external_ref=body.external_ref,
    )
    # Chưa ghép được hóa đơn -> nhắc chụp bill qua email (fire-and-forget,
    # SMTP không chặn response; không cấu hình SMTP thì bỏ qua im lặng)
    if not duplicate and invoice is None and tx.status == TxStatus.PENDING_RECEIPT:
        threading.Thread(
            target=send_receipt_reminder, args=(user, tx), daemon=True
        ).start()
    return {
        "stored": not duplicate,
        "duplicate": duplicate,
        "transaction": tx,
        "matched_invoice_id": invoice.id if invoice else "",
    }


@router.get("/transactions")
def list_transactions(
    status: Optional[TxStatus] = None,
    direction: Optional[TxDirection] = None,
    limit: int = 50,
    user: User = Depends(current_user_local),
) -> list[PaymentTransaction]:
    """Danh sách giao dịch của user.

    GET ?status=pending_receipt -> feed "chờ upload hóa đơn" (notification feed).
    """
    limit = max(1, min(limit, 200))
    return _canonical("tx_repo", "tx_repo").list(
        user_id=user.id, status=status, direction=direction, limit=limit
    )


@router.post("/transactions/{tx_id}/dismiss")
def dismiss_transaction(tx_id: str, user: User = Depends(current_user_local)) -> dict:
    """Bỏ qua nhắc nhở (không cần hóa đơn cho giao dịch này)."""
    repo = _canonical("tx_repo", "tx_repo")
    tx = repo.get(tx_id, user_id=user.id)
    if not tx:
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    repo.set_status(tx_id, TxStatus.DISMISSED, user_id=user.id)
    return {"dismissed": True, "transaction": repo.get(tx_id, user_id=user.id)}


@router.post("/transactions/{tx_id}/match")
def manual_match(tx_id: str, body: MatchRequest, user: User = Depends(current_user_local)) -> dict:
    """Ghép tay giao dịch với hóa đơn (user tự biết 2 cái cùng nhau)."""
    tx_repo_local = _canonical("tx_repo", "tx_repo")
    tx = tx_repo_local.get(tx_id, user_id=user.id)
    if not tx:
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    invoice = _canonical("invoice_repo", "repo").get(body.invoice_id, user_id=user.id)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    link(tx_repo_local, _canonical("invoice_repo", "repo"), tx, invoice)
    return {"matched": True, "transaction": tx_repo_local.get(tx_id, user_id=user.id)}
