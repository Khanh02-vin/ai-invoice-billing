"""Web Push (PWA) — thông báo native trên điện thoại khi có giao dịch chờ hóa đơn.

Cùng triết lý với `src/mail.py`: best-effort, không raise, thiếu cấu hình thì
im lặng bỏ qua. Cần `pip install pywebpush` + cặp VAPID key trong .env
(sinh bằng `python3 scripts/gen_vapid_keys.py`).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .config import get_settings
from .store.push import PushSubscriptionRepository

logger = logging.getLogger("invoice.push")

try:  # pywebpush là dependency tùy chọn — thiếu vẫn chạy, chỉ mất push
    from pywebpush import webpush
except ImportError:  # pragma: no cover
    webpush = None

# Test hook: ép repo riêng (memory) — None nghĩa là dùng repo mặc định.
_repo_override: Optional[PushSubscriptionRepository] = None


def set_repo(repo: Optional[PushSubscriptionRepository]) -> None:
    global _repo_override
    _repo_override = repo


def _repo() -> PushSubscriptionRepository:
    if _repo_override is not None:
        return _repo_override
    # Ưu tiên repo gắn ở app module (pattern tx_repo) để test patch được.
    try:
        from . import app as app_module
        repo = getattr(app_module, "push_repo", None)
        if repo is not None:
            return repo
    except Exception:
        pass
    return PushSubscriptionRepository(get_settings().database_path)


def push_available() -> bool:
    """Push gửi được khi có thư viện + cặp VAPID key."""
    return webpush is not None and get_settings().push_enabled


def send_push_to_user(user_id: str, title: str, body: str, url: str = "/") -> int:
    """Gửi push tới mọi thiết bị đã đăng ký của user. Trả về số thiết bị thành công.

    Subscription hết hạn (push service trả 404/410) bị xoá để khỏi thử lại.
    """
    settings = get_settings()
    if webpush is None or not settings.push_enabled or not user_id:
        return 0
    try:
        subs = _repo().list_for_user(user_id)
    except Exception:
        logger.exception("Push: không đọc được subscription")
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
            sent += 1
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # Thiết bị đã gỡ / subscription hết hạn
                try:
                    _repo().delete(user_id, sub.get("endpoint", ""))
                except Exception:
                    logger.exception("Push: không xoá được subscription hết hạn")
            else:
                logger.warning(
                    "Push thất bại -> %s", (sub.get("endpoint") or "")[:48], exc_info=True
                )
    return sent
