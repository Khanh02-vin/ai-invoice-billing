"""Push router — đăng ký nhận thông báo đẩy (PWA) + gửi thử.

Routes:
- GET  /push/vapid-public-key  -> khoá công khai để frontend subscribe
- POST /push/subscribe         -> lưu subscription của thiết bị hiện tại
- POST /push/unsubscribe       -> xoá subscription của thiết bị
- POST /push/test              -> gửi 1 thông báo thử tới mọi thiết bị của user

Dùng current_user_local (copy pattern từ billing.py/payments.py) để tránh import cycle.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ..auth.security import decode_token
from ..config import get_settings
from ..errors import AppError
from ..models import User
from ..push import push_available, send_push_to_user
from ..store.sql_repository import Database, PushSubscriptionRepository, get_database

router = APIRouter(prefix="/push", tags=["push"])
bearer = HTTPBearer(auto_error=False)

# Lazy: resolve qua app module khi request time (pattern payments.py) để test patch được
_db: Database | None = None


def _get_db() -> Database:
    global _db
    try:
        from .. import app as app_module

        app_db = getattr(getattr(app_module, "users", None), "db", None)
        if app_db is not None:
            return app_db
    except Exception:
        pass
    if _db is None:
        _db = get_database(get_settings().database_path)
    return _db


def _get_push_repository():
    try:
        from .. import app as app_module

        app_repo = getattr(app_module, "push_repo", None)
        if app_repo is not None:
            return app_repo
    except Exception:
        pass
    return PushSubscriptionRepository(_get_db())


def current_user_local(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    try:
        from .. import app as app_module

        app_users = getattr(app_module, "users", None)
        if app_users is not None and hasattr(app_users, "get") and hasattr(app_users, "get_by_username"):
            if not credentials:
                raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
            user_id = decode_token(credentials.credentials)
            user = app_users.get(user_id) if user_id else None
            if not user:
                raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
            if not user.verified:
                raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
            return user
    except AppError:
        raise
    except Exception:
        pass
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    user = _get_db().get_session().get(User, user_id)
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=8, max_length=512)
    auth: str = Field(min_length=4, max_length=512)


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2048)
    keys: PushKeys


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(default="", max_length=2048)


@router.get("/vapid-public-key")
def vapid_public_key(user: User = Depends(current_user_local)) -> dict:
    """Khoá công khai để frontend gọi pushManager.subscribe()."""
    settings = get_settings()
    return {"enabled": settings.push_enabled, "key": settings.vapid_public_key}


@router.post("/subscribe")
def subscribe(body: PushSubscribeRequest, user: User = Depends(current_user_local)) -> dict:
    _get_push_repository().save(
        user.id, {"endpoint": body.endpoint, "keys": body.keys.model_dump()}
    )
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(body: PushUnsubscribeRequest, user: User = Depends(current_user_local)) -> dict:
    removed = _get_push_repository().delete(user.id, body.endpoint)
    return {"ok": True, "removed": removed}


@router.post("/test")
def send_test(user: User = Depends(current_user_local)) -> dict:
    """Gửi thông báo thử tới mọi thiết bị — dùng để kiểm tra cấu hình trên điện thoại."""
    if not push_available():
        raise AppError(
            "PUSH_DISABLED",
            "Server chưa cấu hình VAPID key (xem scripts/gen_vapid_keys.py).",
            status=503,
        )
    sent = send_push_to_user(
        user.id, "Invoice & Billing", "Thông báo thử — cấu hình push đã hoạt động!", url="/"
    )
    return {"sent": sent}
