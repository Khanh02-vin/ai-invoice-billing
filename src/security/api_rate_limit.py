"""Rate-limit dependencies cho FastAPI (endpoint-specific).

Nguyên tắc:
- Không tin X-Forwarded-For từ client trực tiếp. Mặc định lấy địa chỉ từ
  scope["client"] (peer kết nối). Chỉ dùng forwarded address nếu immédiat peer
  nằm trong danh sách trusted proxy đã cấu hình.
- Dùng RateLimiter hiện có (sliding window, in-memory hoặc SQLite).
- Ném RateLimitExceeded (AppError + headers) khi vượt giới hạn; handler trong
  src/app.py trả JSON với Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining.
- Áp dụng qua Depends() trên từng route, không dùng middleware toàn cục để
  tránh giới hạn các route pub (health, metrics, CORS preflight...).
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

from fastapi import Depends, Request

from ..config import get_settings
from ..errors import AppError
from ..security.rate_limit import RateLimiter, get_default_limiter


class RateLimitExceeded(AppError):
    """Lỗi 429 kèm headers chuẩn cho rate limit."""

    def __init__(
        self,
        *,
        limit: int,
        remaining: int,
        retry_after: int,
    ) -> None:
        super().__init__(
            code="TOO_MANY_REQUESTS",
            message="Quá nhiều yêu cầu. Vui lòng thử lại sau.",
            status=429,
            details={
                "limit": limit,
                "remaining": remaining,
                "retry_after": retry_after,
            },
        )
        self.limit = limit
        self.remaining = remaining
        self.retry_after = retry_after


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    max_requests: int
    window_seconds: int


def _client_ip_from_scope(scope: dict, trusted_proxies: set[str] | None) -> str:
    """Lấy IP client đáng tin cậy.

    Ưu tiên scope["client"] (peer kết nối). Chỉ dùng X-Forwarded-For khi
    immediate peer nằm trong trusted_proxies. Nếu không có trusted proxy,
    X-Forwarded-For bị bỏ qua hoàn toàn (client có thể giả bộ).
    """
    client = scope.get("client")
    client_host = client[0] if client else "unknown"

    if not trusted_proxies:
        return client_host

    headers = scope.get("headers", [])
    headers_dict = {k.decode(): v.decode() for k, v in headers}
    xff = headers_dict.get("x-forwarded-for")
    if not xff:
        return client_host

    # Chỉ dùng X-Forwarded-For khi immediate peer là trusted proxy.
    if client_host not in trusted_proxies:
        return client_host

    # Lấy phần tử đầu tiên (client gốc) từ X-Forwarded-For.
    try:
        forwarded = xff.split(",")[0].strip()
        ipaddress.ip_address(forwarded)
        return forwarded
    except ValueError:
        return client_host


def _parse_policy(raw: str) -> RateLimitPolicy:
    try:
        max_str, window_str = str(raw).split(":", 1)
        return RateLimitPolicy(
            name=raw,
            max_requests=int(max_str),
            window_seconds=int(window_str),
        )
    except (ValueError, AttributeError):
        return RateLimitPolicy(name=raw, max_requests=10, window_seconds=60)


def _trusted_proxies() -> set[str]:
    raw = os.getenv("RATE_LIMIT_TRUSTED_PROXIES", "")
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def rate_limit_dependency(
    policy_name: str,
    *,
    use_user_id: bool = False,
):
    """Tạo FastAPI dependency kiểm tra rate limit cho một endpoint.

    Args:
        policy_name: tên trường trong Settings (ví dụ rate_limit_auth_login).
        use_user_id: nếu True và user_id được cung cấp, dùng user_id làm khóa
          chính (gộp với IP). Nếu không có user_id, fallback về IP duy nhất.
    """

    async def _dependency(
        request: Request,
        user_id: str | None = None,
    ) -> None:
        settings = get_settings()
        raw_policy = getattr(settings, policy_name, "10:60")
        policy = _parse_policy(raw_policy)

        limiter = get_default_limiter()
        client_ip = _client_ip_from_scope(request.scope, _trusted_proxies())

        if use_user_id and user_id:
            key = limiter.make_key(client_ip, policy_name, user_id=user_id)
        else:
            key = limiter.make_key(client_ip, policy_name)

        allowed = limiter.is_allowed(key, policy.max_requests, policy.window_seconds)
        remaining = limiter.get_remaining(key, policy.max_requests, policy.window_seconds)

        if not allowed:
            raise RateLimitExceeded(
                limit=policy.max_requests,
                remaining=remaining,
                retry_after=policy.window_seconds,
            )

        # Lưu vào scope state để ASGI middleware có thể inject header vào
        # response thành công (200) nếu muốn.
        scope_state = request.scope.setdefault("state", {})
        scope_state["rate_limit_info"] = {
            "limit": policy.max_requests,
            "remaining": remaining,
            "retry_after": policy.window_seconds,
            "policy": policy_name,
        }

    return _dependency
