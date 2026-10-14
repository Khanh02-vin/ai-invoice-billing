"""Auth hardening router: refresh/revoke, email verification, password reset.

Prefix: /auth
Tags: auth-ext
Email-dependent endpoints fail closed with HTTP 503 when SMTP is unavailable.
Tokens are never returned in API responses.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from ..auth.security import (
    create_email_token,
    create_refresh_token,
    create_token,
    decode_refresh_token,
    decode_token,
    hash_password,
)
from ..config import get_settings
from ..errors import AppError
from ..mail import send_mail, user_email
from ..models import User
from ..store.sql_repository import Database, UserRepository, get_database

router = APIRouter(prefix="/auth", tags=["auth-ext"])

bearer = HTTPBearer(auto_error=False)

# Lazy: resolve via app module at request time
_db: Database | None = None


def _get_db() -> Database:
    global _db
    try:
        from .. import app as app_module

        app_users = getattr(app_module, "users", None)
        if getattr(app_users, "db", None) is not None:
            return app_users.db
    except Exception:
        pass
    if _db is None:
        _db = get_database(get_settings().database_path)
    return _db


def _get_repo() -> UserRepository:
    try:
        from .. import app as app_module

        app_users = getattr(app_module, "users", None)
        if app_users is not None and hasattr(app_users, "create_email_verification"):
            return app_users
        if app_users is not None and getattr(app_users, "db", None) is not None:
            return UserRepository(app_users.db)
    except Exception:
        pass
    return UserRepository(_get_db())


def current_user_local(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    """Resolve user from JWT. 401 if missing/invalid."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    repo = _get_repo()
    user = repo.get(user_id)
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    return user


# ---- request/response models ----


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    username: str


class ForgotPasswordRequest(BaseModel):
    username: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str


# ---- Email helpers ----


def send_verification_email(user: User, repo: UserRepository, base_url: str = "") -> bool:
    """Tạo token xác minh mới + gửi email kèm link bấm-một-lần. False nếu không gửi được.

    `repo` truyền tường minh (không dùng global) để register trong app.py và router
    dùng đúng repository của caller — test patch in-memory vẫn chạy đúng.
    """
    to = user_email(user)
    if not to or not get_settings().email_enabled:
        return False
    token = create_email_token()
    expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    repo.create_email_verification(user.id, token, expires_at)
    lines = [f"Xin chào {user.username},", "", "Bấm link sau để xác nhận địa chỉ email:"]
    if base_url:
        lines.append(f"{base_url}/auth/verify-email/confirm?token={token}")
    lines += [
        "",
        "Hoặc gửi mã này tới POST /auth/verify-email:",
        token,
        "",
        "Link/mã hết hạn sau 24 giờ.",
    ]
    return send_mail(to, "Xác nhận email — Invoice & Billing", "\n".join(lines))


# ---- Endpoints ----


@router.post("/verify-email")
async def verify_email(payload: VerifyEmailRequest):
    """Verify email via token. Marks user as verified."""
    repo = _get_repo()
    user_id = repo.consume_email_verification(payload.token)
    if not user_id:
        raise AppError(
            "INVALID_TOKEN", "Token xac nhan email khong hop le hoac da het han.", status=400
        )
    return {"message": "Da xac nhan email thanh cong."}


@router.get("/verify-email/confirm", response_class=HTMLResponse)
async def confirm_email_get(token: str):
    """Endpoint bạn bấm trong email link — xác nhận và trả HTML thân thiện."""
    # consume_email_verification đã SET verified=1 + đánh dấu used -> single-use.
    repo = _get_repo()
    user_id = repo.consume_email_verification(token)
    if not user_id:
        return _verify_page("Xác minh thất bại", "Token không hợp lệ hoặc đã hết hạn.", False)
    return _verify_page(
        "Xác nhận thành công", "Email đã được xác minh. Bạn có thể đăng nhập.", True
    )


def _verify_page(title: str, body: str, ok: bool) -> HTMLResponse:
    emoji = "✅" if ok else "❌"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title></head>
<body style="font-family:sans-serif;max-width:420px;margin:40px auto;text-align:center">
<h1>{emoji} {title}</h1><p>{body}</p>
</body></html>""",
        status_code=200 if ok else 400,
    )


@router.post("/resend-verification")
async def resend_verification(payload: ResendVerificationRequest, request: Request):
    """Tạo + gửi email xác minh thật (503 nếu chưa có SMTP)."""
    settings = get_settings()
    if not settings.email_enabled:
        raise AppError("EMAIL_SERVICE_UNAVAILABLE", "Email service is unconfigured.", status=503)

    repo = _get_repo()
    user = repo.get_by_username(payload.username)
    if not user:
        raise AppError("NOT_FOUND", "Ten nguoi dung khong ton tai.", status=404)
    if user.verified:
        raise AppError("ALREADY_VERIFIED", "Tai khoan da duoc xac nhan.", status=400)
    base_url = settings.app_url or str(request.base_url).rstrip("/")
    sent = send_verification_email(user, repo, base_url=base_url)
    if not sent:
        raise AppError(
            "EMAIL_SEND_FAILED", "Khong gui duoc email xac nhan, thu lai sau.", status=502
        )
    return {"message": "Email xac nhan da duoc gui qua email."}


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    """Gửi email chứa token đặt lại mật khẩu (best-effort: phản hồi luôn giống
    nhau để không lộ username có tồn tại hay không)."""
    settings = get_settings()
    if not settings.email_enabled:
        raise AppError("EMAIL_SERVICE_UNAVAILABLE", "Email service is unconfigured.", status=503)

    repo = _get_repo()
    user = repo.get_by_username(payload.username)
    if not user:
        # Don't reveal whether username exists.
        return {
            "message": "Neu ten nguoi dung ton tai, email dat lai mat khau se duoc gui qua email."
        }
    token = create_email_token()
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    repo.create_password_reset(user.id, token, expires_at)
    to = user_email(user)
    if to:
        send_mail(
            to,
            "Đặt lại mật khẩu — Invoice & Billing",
            f"Xin chào {user.username},\n\n"
            "Gửi mã sau tới POST /auth/reset-password kèm mật khẩu mới:\n\n"
            f"{token}\n\n"
            "Mã hết hạn sau 1 giờ. Nếu bạn không yêu cầu, hãy bỏ qua email này.",
        )
    # Keep the response generic whether or not the user exists.
    return {"message": "Neu ten nguoi dung ton tai, email dat lai mat khau se duoc gui qua email."}


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    """Reset password via token."""
    user_repo = _get_repo()
    db = getattr(user_repo, "db", None)
    user_id = user_repo.consume_password_reset(payload.token)
    if not user_id:
        raise AppError(
            "INVALID_TOKEN", "Token dat lai mat khau khong hop le hoac da het han.", status=400
        )
    if not user_repo.update_password(user_id, hash_password(payload.new_password)):
        raise AppError("INVALID_TOKEN", "Token dat lai mat khau khong hop le hoac da het han.", status=400)
    if hasattr(user_repo, "db"):
        with user_repo.db.get_session() as session:
            from sqlmodel import select

            from ..models import RefreshToken

            tokens = session.exec(select(RefreshToken).where(RefreshToken.user_id == user_id)).all()
            for token in tokens:
                token.revoked = True
                session.add(token)
            session.commit()
    return {"message": "Dat lai mat khau thanh cong."}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest):
    """Refresh access token via refresh token."""
    decoded = decode_refresh_token(payload.refresh_token)
    if not decoded:
        raise AppError("INVALID_TOKEN", "Refresh token khong hop le.", status=401)
    user_id, jti = decoded
    user_repo = _get_repo()
    valid_user_id = user_repo.is_refresh_token_valid(jti)
    if not valid_user_id or valid_user_id != user_id:
        raise AppError("INVALID_TOKEN", "Refresh token khong hop le hoac da thu hoi.", status=401)
    # Issue new refresh token (rotation).
    new_jti = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    user_repo.revoke_refresh_token(jti)
    user_repo.store_refresh_token(new_jti, user_id, expires_at)
    new_refresh = create_refresh_token(user_id, new_jti)
    new_access = create_token(user_id)
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/revoke")
async def revoke(payload: RefreshRequest):
    """Revoke a refresh token."""
    decoded = decode_refresh_token(payload.refresh_token)
    if not decoded:
        # Gracefully return success to avoid token enumeration.
        return {"message": "Refresh token da duoc thu hoi."}
    _, jti = decoded
    user_repo = _get_repo()
    user_repo.revoke_refresh_token(jti)
    return {"message": "Refresh token da duoc thu hoi."}
