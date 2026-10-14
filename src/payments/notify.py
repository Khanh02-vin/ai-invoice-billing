"""Nhắc chụp hóa đơn — 2 kênh best-effort:
- Email qua SMTP (`src/mail.py`): không cấu hình SMTP / user không có email -> im lặng.
- Push native PWA (`src/push.py`): không cấu hình VAPID key -> im lặng.
Kênh gọi: router /payments/ingest (thread fire-and-forget) + IMAP poller (thread nền).
"""

from __future__ import annotations

from ..models import User
from ..domain.payments import PaymentTransaction
from ..mail import send_mail, user_email


def send_receipt_reminder(user: User, tx: PaymentTransaction) -> bool:
    """Nhắc upload hóa đơn cho 1 giao dịch chưa ghép.

    Trả về True nếu email gửi được (giữ semantics cũ); push là kênh phụ,
    gửi độc lập với email.
    """
    amount = f"{tx.amount:,.0f} {tx.currency}"
    subject = f"[Nhắc] Hoàn tất chứng từ cho giao dịch {amount}"
    where = f" tại {tx.merchant}" if tx.merchant else ""
    when = f" ({tx.occurred_at})" if tx.occurred_at else ""
    body = (
        f"Bạn vừa chi {amount}{where}{when}.\n\n"
        "Chưa tìm thấy hóa đơn khớp số tiền này.\n\n"
        "Nếu có hóa đơn, hãy tải ảnh hóa đơn lên app để đối soát. "
        "Nếu không có hóa đơn, mở thông báo giao dịch trong app để chụp ảnh sản phẩm và xác nhận vị trí.\n"
    )

    # Push notification native (PWA) — best-effort, không phụ thuộc email.
    try:
        from ..push import send_push_to_user

        send_push_to_user(
            user.id,
            subject,
            f"Chưa thấy hóa đơn khớp {amount}. Tải hóa đơn hoặc chụp ảnh sản phẩm trong app.",
            url=f"/?transaction_id={tx.id}",
        )
    except Exception:
        pass

    to = user_email(user)
    if not to:
        return False
    # send_mail tự kiểm tra settings.email_enabled — không cấu hình SMTP thì trả False.
    return send_mail(to, subject, body)
