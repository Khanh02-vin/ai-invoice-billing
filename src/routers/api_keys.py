"""API keys cho bên thứ ba — tạo / liệt kê / hủy.

Quản lý key luôn cần JWT (API key không tự quản lý được — route /api-keys nằm
ngoài SCOPE_RULES nên authenticate() đã chặn 403). Plaintext trả về đúng 1 lần
lúc tạo; các lần list sau chỉ thấy prefix `sk_live_ab12…`.

Sau khi tạo, bên thứ ba gọi API kèm header `X-API-Key: sk_live_...` — chỉ chạm
được các route trong SCOPE_RULES (src/security/api_keys.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.security import decode_token
from ..config import get_settings
from ..errors import AppError
from ..models import ApiKey, ApiKeyCreate, ApiKeyCreated, ApiKeyPublic, User
from ..security.api_keys import KNOWN_SCOPES, generate_key
from ..store.sql_repository import ApiKeyRepository, Database, get_database

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
bearer = HTTPBearer(auto_error=False)

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


def _get_repository() -> ApiKeyRepository:
    try:
        from .. import app as app_module

        repo = getattr(app_module, "api_keys_repo", None)
        if repo is not None:
            return repo
    except Exception:
        pass
    return ApiKeyRepository(_get_db())


def current_user_jwt(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)
) -> User:
    """Quản lý key chỉ cho JWT bearer — API key không được quản lý API key."""
    if (request.headers.get("X-API-Key") or "").strip():
        raise AppError("FORBIDDEN", "API key không được quản lý API key.", status=403)
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    try:
        from .. import app as app_module

        users = getattr(app_module, "users", None)
        if users is not None:
            user_id = decode_token(credentials.credentials)
            user = users.get(user_id) if user_id else None
            if not user:
                raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
            if not user.verified:
                raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
            return user
    except AppError:
        raise
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    with _get_db().get_session() as session:
        user = session.get(User, user_id)
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


def _to_public(key: ApiKey) -> ApiKeyPublic:
    return ApiKeyPublic(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        scopes=[s.strip() for s in (key.scopes or "").split(",") if s.strip()],
        rate_limit_per_min=key.rate_limit_per_min,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        is_active=key.is_active,
        created_at=key.created_at,
    )


@router.post("", response_model=ApiKeyCreated)
def create_key(body: ApiKeyCreate, user: User = Depends(current_user_jwt)) -> ApiKeyCreated:
    unknown = [s for s in body.scopes if s not in KNOWN_SCOPES]
    if unknown:
        raise AppError(
            "INVALID_SCOPES",
            f"Scope không hợp lệ: {', '.join(unknown)}. Hợp lệ: {', '.join(KNOWN_SCOPES)}.",
            status=400,
        )
    if not body.scopes:
        raise AppError("INVALID_SCOPES", "Cần ít nhất một scope.", status=400)

    plaintext, key_hash, key_prefix = generate_key()
    key = ApiKey(
        user_id=user.id,
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=",".join(body.scopes),
        rate_limit_per_min=body.rate_limit_per_min,
        expires_at=body.expires_at,
    )
    saved = _get_repository().create(key)
    return ApiKeyCreated(**_to_public(saved).model_dump(), key=plaintext)


@router.get("", response_model=list[ApiKeyPublic])
def list_keys(user: User = Depends(current_user_jwt)) -> list[ApiKeyPublic]:
    return [_to_public(k) for k in _get_repository().list_for_user(user.id)]


@router.delete("/{key_id}")
def revoke_key(key_id: str, user: User = Depends(current_user_jwt)) -> dict:
    if not _get_repository().revoke(key_id, user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy API key.", status=404)
    return {"revoked": True}
