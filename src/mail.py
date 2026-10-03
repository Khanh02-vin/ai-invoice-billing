"""Gửi email SMTP dùng chung — notify (nhắc chụp hóa đơn) và auth (xác minh
email / đặt lại mật khẩu). stdlib smtplib, không thêm dependency.

Chưa cấu hình SMTP (EMAIL_SMTP_HOST/PORT) -> send_mail trả False; caller quyết
định (503 fail-closed ở auth, im lặng bỏ qua ở notify).
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import get_settings
from .domain.models import User

logger = logging.getLogger("invoice.mail")


def user_email(user: User) -> str:
    """Địa chỉ nhận: cột email, fallback username nếu username chính là email."""
    if user.email:
        return user.email
    return user.username if "@" in user.username else ""


def send_mail(to: str, subject: str, body: str) -> bool:
    """Gửi 1 email text. True nếu SMTP đã nhận (best-effort, lỗi được log lại)."""
    settings = get_settings()
    if not settings.email_enabled or not to:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.email_from or settings.smtp_user
    msg["To"] = to
    msg.set_content(body)

    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                _login(s, settings)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                try:
                    s.starttls()
                    s.ehlo()
                except Exception:
                    pass  # server nội bộ (dev) không bật TLS
                _login(s, settings)
                s.send_message(msg)
        return True
    except Exception:
        logger.warning("Gửi email thất bại -> %s", to, exc_info=True)
        return False


def _login(s, settings) -> None:
    if settings.smtp_user:
        s.login(settings.smtp_user, settings.smtp_pass)
