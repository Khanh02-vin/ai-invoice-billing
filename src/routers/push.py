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
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from ..auth.security import decode_token
from ..config import get_settings
from ..domain.models import User
from ..errors import AppError
from ..push import push_available, send_push_to_user
from ..store.push import PushSubscriptionRepository
from ..store.users import UserRepository

router = APIRouter(prefix="/push", tags=["push"])
bearer = HTTPBearer(auto_error=False)

# Lazy: resolve qua app module khi request time (pattern payments.py) để test patch được
user_repo: UserRepository | None = None
push_repo: PushSubscriptionRepository | None = None


def _canonical(module_attr: str, fallback_import: str):
    repo = globals()[module_attr]
    if repo is not None:
        return repo
    from .. import app as app_module
    return getattr(app_module, fallback_import)


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
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
def subscribe(
    body: PushSubscribeRequest, user: User = Depends(current_user_local)
) -> dict:
    _canonical("push_repo", "push_repo").save(
        user.id, {"endpoint": body.endpoint, "keys": body.keys.model_dump()}
    )
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(
    body: PushUnsubscribeRequest, user: User = Depends(current_user_local)
) -> dict:
    removed = _canonical("push_repo", "push_repo").delete(user.id, body.endpoint)
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
        user.id,
        "Invoice & Billing",
        "Thông báo thử — cấu hình push đã hoạt động!",
        url="/",
    )
    return {"sent": sent}
