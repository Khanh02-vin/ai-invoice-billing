"""Bảo mật: hash mật khẩu (stdlib pbkdf2) + JWT (PyJWT) + refresh/tokens.
ponytail: pbkdf2_hmac stdlib thay bcrypt — đủ an toàn, không thêm dep nặng."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from ..config import get_settings

_ALGO = "HS256"
_ITERATIONS = 100_000
_REFRESH_DAYS = 30


def hash_password(password: str) -> str:
    """Băm mật khẩu: salt(16) + pbkdf2-sha256(100k lần), base64."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return base64.b64encode(salt + dk).decode()


def verify_password(password: str, stored: str) -> bool:
    """So sánh mật khẩu với hash đã lưu, chống timing attack."""
    try:
        raw = base64.b64decode(stored)
        salt, dk = raw[:16], raw[16:]
        new = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
        return secrets.compare_digest(dk, new)
    except Exception:
        return False


def create_token(user_id: str, days: int | None = None) -> str:
    """Tạo JWT hết hạn sau N ngày."""
    settings = get_settings()
    if days is None:
        days = settings.jwt_expiry_days
    payload = {"sub": user_id, "exp": datetime.now(UTC) + timedelta(days=days)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> str | None:
    """Giải mã JWT, trả về user_id hoặc None nếu sai/hết hạn."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except Exception:
        return None


# ---- Refresh tokens ----


def create_refresh_token(user_id: str, jti: str) -> str:
    """Tạo refresh token JWT (short-lived, 30 days) chứa jti."""
    settings = get_settings()
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": "refresh",
        "exp": datetime.now(UTC) + timedelta(days=_REFRESH_DAYS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_refresh_token(token: str) -> tuple[str, str] | None:
    """Giải mã refresh token, trả về (user_id, jti) hoặc None nếu sai."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "refresh":
            return None
        user_id = payload.get("sub")
        jti = payload.get("jti")
        if not user_id or not jti:
            return None
        return user_id, jti
    except Exception:
        return None


# ---- Email / reset token helpers ----


def create_email_token() -> str:
    """Tạo token URL-safe ngẫu nhiên cho email verification."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash token bằng SHA-256 để lưu vào DB."""
    return hashlib.sha256(token.encode()).hexdigest()
