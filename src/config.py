"""Cấu hình theo môi trường với fail-fast khi production thiếu secret.

Nguyên tắc (PLAN mục 3):
- Không commit secret, không dùng default JWT secret ở production.
- Production config từ chối JWT_SECRET mặc định hoặc thiếu.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


DEFAULT_DEV_SECRET = "dev-secret-change-me-please-32bytes-min"
_DEFAULT_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list_env(name: str, default: List[str]) -> List[str]:
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
    open_registration: bool = field(default_factory=lambda: _bool_env("OPEN_REGISTRATION", default=False))

    # CORS
    cors_origins: List[str] = field(default_factory=lambda: _list_env("CORS_ORIGINS", _DEFAULT_DEV_ORIGINS))

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

    # IMAP poller — đọc email thông báo giao dịch ngân hàng/ví -> /payments/ingest
    imap_host: str = field(default_factory=lambda: os.getenv("IMAP_HOST", ""))
    imap_port: int = field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")))
    imap_user: str = field(default_factory=lambda: os.getenv("IMAP_USER", ""))
    imap_pass: str = field(default_factory=lambda: os.getenv("IMAP_PASS", ""))
    imap_folder: str = field(default_factory=lambda: os.getenv("IMAP_FOLDER", "INBOX"))
    imap_interval_seconds: int = field(default_factory=lambda: int(os.getenv("IMAP_INTERVAL_SECONDS", "60")))
    # Gắn email -> user (VD "linh@gmail.com,user1"); rỗng: mọi email gán cho
    # mọi user verified (chỉ dùng khi inbox là của chính người dùng).
    imap_user_map: str = field(default_factory=lambda: os.getenv("IMAP_USER_MAP", ""))

    @property
    def imap_enabled(self) -> bool:
        """IMAP poller bật khi có host + user."""
        return bool(self.imap_host and self.imap_user)

    # Billing
    billing_provider: str = field(default_factory=lambda: os.getenv("BILLING_PROVIDER", "mock"))

    # Upload
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))))  # 10 MB
    allowed_mime_types: List[str] = field(
        default_factory=lambda: _list_env(
            "ALLOWED_MIME_TYPES",
            ["application/pdf", "image/png", "image/jpeg", "image/tiff", "text/plain"],
        )
    )
    max_pdf_pages: int = field(default_factory=lambda: int(os.getenv("MAX_PDF_PAGES", "20")))
    upload_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("UPLOAD_TIMEOUT_SECONDS", "60")))

    # Storage
    storage_dir: str = field(default_factory=lambda: os.getenv("STORAGE_DIR", "data/storage"))
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "invoices.db"))

    # LLM
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.qwencoder.cloud/api/v1"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "qwen3.7-max"))
    llm_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT_SECONDS", "30")))
    llm_max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    llm_cost_cap_usd: float = field(default_factory=lambda: float(os.getenv("LLM_COST_CAP_USD", "0.50")))

    # Privacy
    privacy_local_only: bool = field(default_factory=lambda: _bool_env("PRIVACY_LOCAL_ONLY", default=False))
    privacy_redact_pii: bool = field(default_factory=lambda: _bool_env("PRIVACY_REDACT_PII", default=True))

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
        if not self.email_enabled:
            import logging
            logging.warning(
                "Production: email transport chưa cấu hình (EMAIL_SMTP_HOST/EMAIL_SMTP_PORT). "
                "Cần cấu hình SMTP trước khi gửi email thực tế."
            )
        if self.billing_provider == "mock":
            import logging
            logging.warning(
                "Production: BILLING_PROVIDER=mock không an toàn cho môi trường thực tế. "
                "Hãy cấu hình stripe|paddle trước khi nhận thanh toán."
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
