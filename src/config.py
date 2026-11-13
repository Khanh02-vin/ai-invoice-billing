"""Cấu hình theo môi trường với fail-fast khi production thiếu secret.

Nguyên tắc (PLAN mục 3):
- Không commit secret, không dùng default JWT secret ở production.
- Production config từ chối JWT_SECRET mặc định hoặc thiếu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_DEV_SECRET = "dev-secret-change-me-please-32bytes-min"
_DEFAULT_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Cấu hình ứng dụng đọc từ biến môi trường."""

    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    debug: bool = field(default_factory=lambda: _bool_env("APP_DEBUG", default=False))

    # Auth
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", DEFAULT_DEV_SECRET))
    jwt_algorithm: str = field(default_factory=lambda: os.getenv("JWT_ALGO", "HS256"))
    jwt_expiry_days: int = field(default_factory=lambda: int(os.getenv("JWT_EXPIRY_DAYS", "7")))
    open_registration: bool = field(
        default_factory=lambda: _bool_env("OPEN_REGISTRATION", default=False)
    )
    # Bản demo không có SMTP: tự đánh dấu đã xác minh để user đăng nhập lại được.
    # Mặc định TẮT — production phải xác minh email thật.
    auto_verify_email: bool = field(
        default_factory=lambda: _bool_env("AUTO_VERIFY_EMAIL", default=False)
    )

    # CORS
    cors_origins: list[str] = field(
        default_factory=lambda: _list_env("CORS_ORIGINS", _DEFAULT_DEV_ORIGINS)
    )

    google_oauth_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_oauth_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_oauth_redirect_uri: str = field(default_factory=lambda: os.getenv("GOOGLE_REDIRECT_URI", ""))

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(
            self.google_oauth_client_id
            and self.google_oauth_client_secret
            and self.google_oauth_redirect_uri
        )

    # Email transport (SMTP). When configured, offline token returns are disabled.
    smtp_host: str = field(default_factory=lambda: os.getenv("EMAIL_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("EMAIL_SMTP_PORT", "0")))
    smtp_user: str = field(default_factory=lambda: os.getenv("EMAIL_SMTP_USER", ""))
    smtp_pass: str = field(default_factory=lambda: os.getenv("EMAIL_SMTP_PASS", ""))
    email_from: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", ""))

    # Base URL công khai của API, dùng dựng link trong email (xác minh / reset).
    # Bỏ trống -> lấy request.base_url (đúng khi request tới thẳng API, nhưng sau
    # reverse proxy phải set tường minh, VD http://127.0.0.1:8000).
    app_url: str = field(default_factory=lambda: os.getenv("APP_URL", ""))

    @property
    def email_enabled(self) -> bool:
        """Email transport is considered enabled when SMTP host is configured."""
        return bool(self.smtp_host and self.smtp_port)

    # Rate limiting (sliding window, per endpoint policy)
    # Format for env: RL_AUTH_LOGIN=5:60 means 5 requests per 60 seconds.
    rate_limit_auth_login: str = field(
        default_factory=lambda: os.getenv("RL_AUTH_LOGIN", "5:60")
    )
    rate_limit_auth_register: str = field(
        default_factory=lambda: os.getenv("RL_AUTH_REGISTER", "3:60")
    )
    rate_limit_auth_resend: str = field(
        default_factory=lambda: os.getenv("RL_AUTH_RESEND", "3:300")
    )

    def rate_limit_policy(self, name: str) -> tuple[int, int]:
        """Trả về (max_requests, window_seconds) cho một chính sách.

        Mô env: "<max_requests>:<window_seconds>".
        """
        raw = getattr(self, name, "10:60")
        try:
            max_str, window_str = str(raw).split(":", 1)
            return int(max_str), int(window_str)
        except (ValueError, AttributeError):
            return 10, 60

    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/spends.db"))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def ensure_production_readiness(self) -> None:
        """Fail-fast: production phải có secret riêng, không dùng wildcard CORS."""
        if not self.is_production:
            return
        if self.jwt_secret in (DEFAULT_DEV_SECRET, ""):
            raise RuntimeError(
                "Production yêu cầu JWT_SECRET riêng (không được để mặc định). "
                "Hãy đặt biến môi trường JWT_SECRET."
            )
        if "*" in self.cors_origins:
            raise RuntimeError(
                "Production không cho phép CORS wildcard. "
                "Hãy đặt CORS_ORIGINS danh sách trắng cụ thể."
            )
        if self.debug:
            raise RuntimeError("Production không cho phép APP_DEBUG=true.")
        if any((self.google_oauth_client_id, self.google_oauth_client_secret, self.google_oauth_redirect_uri)) and not self.google_oauth_enabled:
            raise RuntimeError(
                "Google OAuth production config requires GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI together."
            )
        if not self.email_enabled:
            import logging

            logging.warning(
                "Production: email transport chưa cấu hình (EMAIL_SMTP_HOST/EMAIL_SMTP_PORT). "
                "Cần cấu hình SMTP trước khi gửi email thực tế."
            )


_settings: Settings | None = None
_overrides: dict = {}


def get_settings() -> Settings:
    """Trả về settings — đọc lại env mỗi lần để test monkeypatch hoạt động."""
    global _settings, _overrides
    # Luôn tạo Settings mới từ env hiện tại (để monkeypatch.setenv có hiệu lực)
    base = Settings()
    if _overrides:
        # Áp dụng override nếu có
        data = {}
        for f in base.__dataclass_fields__:
            data[f] = getattr(base, f)
        data.update(_overrides)
        base = Settings(**data)
    _settings = base
    return _settings


def override_settings(**kwargs) -> None:
    """Override settings (dùng cho test)."""
    global _overrides
    _overrides = dict(kwargs)


def reset_settings() -> None:
    """Reset cache (dùng cho test)."""
    global _settings, _overrides
    _settings = None
    _overrides = {}
