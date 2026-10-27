"""API key cho bên thứ ba — xác thực thay JWT, giới hạn theo scope.

Key format: `sk_live_<token ngẫu nhiên>`. DB chỉ lưu hash SHA-256; plaintext
chỉ trả về đúng một lần lúc tạo. Cách dùng:

    curl -H "X-API-Key: sk_live_..." https://.../invoices

current_user (src/app.py) ưu tiên X-API-Key khi header có mặt; scope kiểm
theo method + path qua SCOPE_RULES — route ngoài danh sách bị chặn 403 với
API key (JWT vẫn vào bình thường).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from ..errors import AppError
from ..models import ApiKey
from .rate_limit import get_default_limiter

# Scope hợp lệ khi tạo key
KNOWN_SCOPES = (
    "invoices:read",
    "invoices:write",
    "payments:read",
    "payments:write",
    "reports:read",
)

# Bề mặt API mở cho bên thứ ba: (scope, method, path_prefix). Path ngoài các
# prefix này (auth, push, api-keys, ...) không gọi được bằng API key.
SCOPE_RULES: tuple[tuple[str, str, str], ...] = (
    ("invoices:read", "GET", "/invoices"),
    ("invoices:write", "POST", "/invoices"),
    ("invoices:write", "PUT", "/invoices"),
    ("invoices:write", "DELETE", "/invoices"),
    ("invoices:write", "POST", "/upload"),
    ("payments:read", "GET", "/payments/transactions"),
    ("payments:write", "POST", "/payments/transactions"),
    ("payments:write", "PUT", "/payments/transactions"),
    ("payments:write", "DELETE", "/payments/transactions"),
    ("reports:read", "GET", "/reports/monthly"),
)

_LAST_USED_REFRESH_SECONDS = 60


def generate_key() -> tuple[str, str, str]:
    """Trả về (plaintext, key_hash, key_prefix) — plaintext chỉ thấy lúc tạo."""
    plaintext = "sk_live_" + secrets.token_urlsafe(24)
    return plaintext, hash_key(plaintext), plaintext[:12]


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def required_scope(method: str, path: str) -> str | None:
    """Scope cần cho method+path; None = route không mở cho API key."""
    for scope, rule_method, prefix in SCOPE_RULES:
        if method == rule_method and path.startswith(prefix):
            return scope
    return None


def _as_utc(value: datetime) -> datetime:
    """SQLite trả datetime naive — coi là UTC để so sánh."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def authenticate(request, repo) -> ApiKey:
    """Xác thực X-API-Key + scope + rate limit per key.

    Raise AppError(401/403/429). Caller (current_user) chỉ gọi khi header có mặt.
    """
    plaintext = (request.headers.get("X-API-Key") or "").strip()
    key = repo.get_by_hash(hash_key(plaintext)) if plaintext else None
    if not key or not key.is_active:
        raise AppError("UNAUTHORIZED", "API key không hợp lệ.", status=401)
    if key.expires_at and _as_utc(key.expires_at) < datetime.now(UTC):
        raise AppError("UNAUTHORIZED", "API key đã hết hạn.", status=401)

    scope = required_scope(request.method, request.url.path)
    if scope is None:
        raise AppError(
            "FORBIDDEN",
            f"API key không được phép truy cập {request.method} {request.url.path}.",
            status=403,
        )
    key_scopes = {s.strip() for s in (key.scopes or "").split(",") if s.strip()}
    if scope not in key_scopes:
        raise AppError("FORBIDDEN", f"API key thiếu scope '{scope}'.", status=403)

    limiter = get_default_limiter()
    if not limiter.is_allowed(f"apikey:{key.id}", key.rate_limit_per_min, 60):
        raise AppError(
            "RATE_LIMITED",
            "Vượt giới hạn request của API key, thử lại sau.",
            status=429,
        )

    # Ghi nhận sử dụng — throttle 1 lần/phút để khỏi ghi DB mỗi request
    now = datetime.now(UTC)
    if key.last_used_at is None or (now - _as_utc(key.last_used_at)).total_seconds() >= _LAST_USED_REFRESH_SECONDS:
        try:
            repo.touch_last_used(key.id)
        except Exception:  # best-effort, không chặn request
            pass
    return key


def user_from_api_key(request, users_repo, api_keys_repo):
    """User cho request có header X-API-Key; None nếu request không dùng API key.

    Dùng chung cho mọi biến thể current_user (app.py, routers/payments.py) —
    raise AppError(401/403/429) khi key không hợp lệ/thiếu scope/vượt limit.
    """
    if not (request.headers.get("X-API-Key") or "").strip():
        return None
    key = authenticate(request, api_keys_repo)
    request.state.api_key = key
    user = users_repo.get(key.user_id) if key.user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "API key không hợp lệ.", status=401)
    return user
