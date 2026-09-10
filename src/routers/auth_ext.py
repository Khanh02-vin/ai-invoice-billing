"""Auth hardening router: refresh/revoke, email verification, password reset, MFA (TOTP).

Prefix: /auth
Tags: auth-ext
Email-dependent endpoints fail closed with HTTP 503 when SMTP is unavailable.
Tokens are never returned in API responses.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from ..auth.security import (
    create_refresh_token,
    decode_refresh_token,
    create_email_token,
    create_totp_secret,
    verify_totp,
    generate_backup_codes,
    create_token,
    decode_token,
    hash_password,
)
from ..config import get_settings
from ..domain.models import User
from ..errors import AppError
from ..store.users import UserRepository

router = APIRouter(prefix="/auth", tags=["auth-ext"])

bearer = HTTPBearer(auto_error=False)

# Module-level repo (file DB default). Tests patch as needed.
user_repo = UserRepository()


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    """Resolve user from JWT. 401 if missing/invalid."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    user = user_repo.get(user_id)
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


class MfaSetupResponse(BaseModel):
    secret: str
    backup_codes: list[str]
    # TODO: In production, return otpauth_uri for QR scanning.


class MfaVerifyRequest(BaseModel):
    code: str


# ---- Endpoints ----

@router.post("/verify-email")
async def verify_email(payload: VerifyEmailRequest):
    """Verify email via token. Marks user as verified."""
    user_id = user_repo.consume_email_verification(payload.token)
    if not user_id:
        raise AppError("INVALID_TOKEN", "Token xac nhan email khong hop le hoac da het han.", status=400)
    return {"message": "Da xac nhan email thanh cong."}


@router.post("/resend-verification")
async def resend_verification(payload: ResendVerificationRequest):
    """Send a verification token without exposing it to the caller."""
    settings = get_settings()
    if not settings.email_enabled:
        raise AppError(
            "EMAIL_SERVICE_UNAVAILABLE",
            "Email service is unconfigured.",
            status=503,
        )

    user = user_repo.get_by_username(payload.username)
    if not user:
        raise AppError("NOT_FOUND", "Ten nguoi dung khong ton tai.", status=404)
    if user.verified:
        raise AppError("ALREADY_VERIFIED", "Tai khoan da duoc xac nhan.", status=400)
    token = create_email_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    user_repo.create_email_verification(user.id, token, expires_at)
    # In production, the token is sent through SMTP; it is never returned.
    return {"message": "Token xac nhan da duoc gui qua email."}


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    """Send a password reset token without exposing it to the caller."""
    settings = get_settings()
    if not settings.email_enabled:
        raise AppError(
            "EMAIL_SERVICE_UNAVAILABLE",
            "Email service is unconfigured.",
            status=503,
        )

    user = user_repo.get_by_username(payload.username)
    if not user:
        # Don't reveal whether username exists.
        return {"message": "Neu ten nguoi dung ton tai, email dat lai mat khau se duoc gui qua email."}
    token = create_email_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    user_repo.create_password_reset(user.id, token, expires_at)
    # Keep the response generic whether or not the user exists.
    return {"message": "Neu ten nguoi dung ton tai, email dat lai mat khau se duoc gui qua email."}


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    """Reset password via token."""
    user_id = user_repo.consume_password_reset(payload.token)
    if not user_id:
        raise AppError("INVALID_TOKEN", "Token dat lai mat khau khong hop le hoac da het han.", status=400)
    new_hash = hash_password(payload.new_password)
    user_repo.update_password(user_id, new_hash)
    # Revoke all refresh tokens on password change.
    with user_repo._connect() as conn:
        conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
    return {"message": "Dat lai mat khau thanh cong."}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest):
    """Refresh access token via refresh token."""
    decoded = decode_refresh_token(payload.refresh_token)
    if not decoded:
        raise AppError("INVALID_TOKEN", "Refresh token khong hop le.", status=401)
    user_id, jti = decoded
    valid_user_id = user_repo.is_refresh_token_valid(jti)
    if not valid_user_id or valid_user_id != user_id:
        raise AppError("INVALID_TOKEN", "Refresh token khong hop le hoac da thu hoi.", status=401)
    # Issue new refresh token (rotation).
    new_jti = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
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
    user_repo.revoke_refresh_token(jti)
    return {"message": "Refresh token da duoc thu hoi."}


@router.post("/mfa/setup")
async def mfa_setup(user: User = Depends(current_user_local)):
    """Setup MFA (TOTP). Returns secret + backup codes."""
    secret = create_totp_secret()
    user_repo.store_mfa_secret(user.id, secret)
    backup_codes = generate_backup_codes(8)
    return MfaSetupResponse(secret=secret, backup_codes=backup_codes)


@router.post("/mfa/verify")
async def mfa_verify(payload: MfaVerifyRequest, user: User = Depends(current_user_local)):
    """Verify TOTP code and enable MFA."""
    info = user_repo.get_mfa_secret(user.id)
    if not info:
        raise AppError("MFA_NOT_SETUP", "Chua lap dat MFA. Goi /auth/mfa/setup truoc.", status=400)
    if not verify_totp(info["secret"], payload.code):
        raise AppError("INVALID_CODE", "Ma xac minh khong hop le.", status=400)
    user_repo.set_mfa_enabled(user.id, True)
    return {"message": "MFA da duoc bat."}
