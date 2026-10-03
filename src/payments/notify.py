"""Email push nhắc chụp hóa đơn — SMTP best-effort, dùng chung `send_mail` trong
`src/mail.py`. Không cấu hình SMTP (hoặc user không có email) -> im lặng bỏ qua.
Kênh: router /payments/ingest (thread fire-and-forget) + IMAP poller (thread nền).
"""
from __future__ import annotations

from ..domain.models import User
from ..domain.payments import PaymentTransaction
from ..mail import send_mail, user_email


def send_receipt_reminder(user: User, tx: PaymentTransaction) -> bool:
    """Nhắc upload hóa đơn cho 1 giao dịch chưa ghép. True nếu gửi được."""
    to = user_email(user)
    if not to:
        return False

    amount = f"{tx.amount:,.0f} {tx.currency}"
    subject = f"[Nhắc] Chụp hóa đơn cho giao dịch {amount}"
    where = f" tại {tx.merchant}" if tx.merchant else ""
    when = f" ({tx.occurred_at})" if tx.occurred_at else ""
    body = (
        f"Bạn vừa chi {amount}{where}{when}.\n\n"
        "Chưa tìm thấy hóa đơn khớp số tiền này.\n"
        "Hãy upload ảnh hóa đơn trong app để hệ thống tự đối soát.\n"
    )
    # send_mail tự kiểm tra settings.email_enabled — không cấu hình SMTP thì trả False.
    return send_mail(to, subject, body)
