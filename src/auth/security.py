"""Bảo mật: hash mật khẩu (stdlib pbkdf2) + JWT (PyJWT).
ponytail: pbkdf2_hmac stdlib thay bcrypt — đủ an toàn, không thêm dep nặng."""
import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-please-32bytes-min")
_ALGO = "HS256"
_ITERATIONS = 100_000


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


def create_token(user_id: str, days: int = 7) -> str:
    """Tạo JWT hết hạn sau N ngày."""
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=days),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def decode_token(token: str) -> str | None:
    """Giải mã JWT, trả về user_id hoặc None nếu sai/hết hạn."""
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGO])
        return payload.get("sub")
    except Exception:
        return None