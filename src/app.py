"""FastAPI app — Khám phá quán ăn theo mức chi tiêu.

Nguyên tắc:
- CORS allowlist; security headers; request ID; structured logging.
- Error contract JSON ổn định; không trả stack trace/PII.
- Health/readiness phân biệt process sống và dependency sẵn sàng.
- Mọi số tiền trả về là **mức chi ghi nhận tại địa điểm**, không phải giá món.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.deps import current_user, get_db, user_repo
from .auth.security import create_refresh_token as create_refresh_jwt
from .auth.security import create_token, hash_password, verify_password
from .config import get_settings
from .errors import AppError, _public_body, register_error_handlers
from .middleware import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    StructuredLoggingMiddleware,
    configure_cors,
)
from .models import Token, User, UserCreate, UserPublic
from .routers.auth_ext import router as auth_ext_router
from .routers.auth_ext import send_verification_email
from .routers.discover import router as discover_router
from .routers.google_oauth import router as google_oauth_router
from .routers.places import router as places_router
from .routers.spends import router as spends_router
from .security.api_rate_limit import RateLimitExceeded, rate_limit_dependency
from .store.sql_repository import UserRepository, init_database

logger = logging.getLogger("spends.app")

settings = get_settings()
settings.ensure_production_readiness()

app = FastAPI(title="Khám phá quán ăn theo mức chi tiêu", version="1.0.0")

# --- Middleware (order matters: outermost first) ---
configure_cors(app, settings.cors_origins)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(StructuredLoggingMiddleware)

# --- Error handlers ---
register_error_handlers(app)


@app.exception_handler(RateLimitExceeded)
async def _handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
    logger.warning("rate_limit_exceeded request_id=%s", request_id)
    return JSONResponse(
        status_code=429,
        content=_public_body(exc.code, exc.message, request_id, exc.details),
        headers={
            "Retry-After": str(exc.retry_after),
            "X-RateLimit-Limit": str(exc.limit),
            "X-RateLimit-Remaining": str(exc.remaining),
        },
    )


# --- Routers ---
app.include_router(auth_ext_router)
app.include_router(google_oauth_router)
app.include_router(places_router)
app.include_router(spends_router)
app.include_router(discover_router)

# --- Initialize Database ---
db = init_database(settings.database_path)


# ---------- Health / Readiness ----------
@app.get("/health")
async def health():
    """Health check — process sống."""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/ready")
async def ready():
    """Readiness check — DB trả lời được."""
    try:
        get_db().ping()
    except Exception:  # pragma: no cover - chỉ xảy ra khi file DB hỏng/ổ khoá
        return JSONResponse(status_code=503, content={"ready": False, "checks": {"database": False}})
    return {"ready": True, "checks": {"database": True}}


# ---------- Auth ----------
# Đăng ký mặc định TẮT khi deploy (ai có link public đều gọi được API).
# Mở bằng env OPEN_REGISTRATION=1 khi cần tạo tài khoản mới.


@app.post("/auth/register", response_model=Token)
async def register(
    data: UserCreate,
    request: Request,
    repo: UserRepository = Depends(user_repo),
    _rl: None = Depends(rate_limit_dependency("rate_limit_auth_register")),
):
    # Đọc settings tại request time để test monkeypatch có hiệu lực
    _settings = get_settings()
    if not _settings.open_registration:
        raise AppError("REGISTRATION_CLOSED", "Đăng ký đang tắt.", status=403)
    if repo.get_by_username(data.username):
        raise AppError("USERNAME_EXISTS", "Tên người dùng đã tồn tại.", status=409)
    if data.email and "@" not in data.email:
        raise AppError("INVALID_EMAIL", "Email không hợp lệ.", status=400)
    user = repo.create(data.username, hash_password(data.password), email=data.email or "")
    # Demo không có SMTP: nếu bật AUTO_VERIFY_EMAIL thì xác minh luôn, tránh việc
    # đăng ký xong dùng được nhưng lần đăng nhập sau bị chặn EMAIL_NOT_VERIFIED.
    if _settings.auto_verify_email:
        repo.set_verified(user.id, True)
        user.verified = True
    jti = secrets.token_urlsafe(24)
    refresh_token = create_refresh_jwt(user.id, jti)
    repo.store_refresh_token(jti, user.id, (datetime.now(UTC) + timedelta(days=30)).isoformat())
    # Gửi email xác minh ngay (best-effort): SMTP chưa cấu hình hoặc user không có
    # email -> verification_sent=False.
    verification_sent = False
    if user.email:
        base_url = _settings.app_url or str(request.base_url).rstrip("/")
        verification_sent = send_verification_email(user, repo, base_url=base_url)
    return {
        "access_token": create_token(user.id),
        "refresh_token": refresh_token,
        "verification_sent": verification_sent,
        "verified": bool(user.verified),
    }


@app.post("/auth/login", response_model=Token)
async def login(
    data: UserCreate,
    repo: UserRepository = Depends(user_repo),
    _rl: None = Depends(rate_limit_dependency("rate_limit_auth_login")),
):
    user = repo.get_by_username(data.username)
    if user is None and "@" in data.username:
        user = repo.get_by_email(data.username)
    if not user or not verify_password(data.password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Sai tên người dùng hoặc mật khẩu.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    jti = secrets.token_urlsafe(24)
    refresh_token = create_refresh_jwt(user.id, jti)
    repo.store_refresh_token(jti, user.id, (datetime.now(UTC) + timedelta(days=30)).isoformat())
    return {"access_token": create_token(user.id), "refresh_token": refresh_token}


@app.get("/auth/me", response_model=UserPublic)
async def me(user: User = Depends(current_user)):
    return UserPublic(id=user.id, username=user.username, email=user.email)


# ---------- Static build (nếu đã build frontend) ----------
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
