"""FastAPI app cho Invoice & Billing System. Auth JWT + đa người dùng.

Nguyên tắc (PLAN mục 3):
- CORS allowlist; security headers; request ID; structured logging.
- Error contract JSON ổn định; không trả stack trace/PII.
- Upload validation: dung lượng, MIME sniff, số trang.
- Health/readiness phân biệt process sống và dependency sẵn sàng.
"""
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .domain.models import (
    Invoice, InvoiceCreate, InvoiceUpdate, MonthlyReport, InvoiceStatus,
    User, UserCreate, UserPublic, Token,
)
from .extract.extractor import extract_invoice
from .store.repository import InvoiceRepository
from .store.users import UserRepository
from .auth.security import hash_password, verify_password, create_token, decode_token, create_refresh_token as create_refresh_jwt
from .errors import register_error_handlers, AppError
from .middleware import (
    RequestIdMiddleware, SecurityHeadersMiddleware, StructuredLoggingMiddleware, configure_cors,
)
from .upload import validate_upload
from .store.storage import LocalFileStorage, generate_storage_key, compute_fingerprint
from .routers.orgs import router as orgs_router
from .routers.search import router as search_router
from .routers.billing import router as billing_router
from .routers.auth_ext import router as auth_ext_router
from .observability import metrics as obs_metrics

settings = get_settings()
settings.ensure_production_readiness()

app = FastAPI(title="Invoice & Billing System", version="3.0.0")

# --- Middleware (order matters: outermost first) ---
configure_cors(app, settings.cors_origins)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(StructuredLoggingMiddleware)

# --- Error handlers ---
register_error_handlers(app)

# --- Routers (Phase 2-4) ---
app.include_router(orgs_router)
app.include_router(search_router)
app.include_router(billing_router)
app.include_router(auth_ext_router)

# --- Observability (Phase 4) ---
@app.get("/metrics")
async def metrics_endpoint():
    return obs_metrics.metrics_response()

repo = InvoiceRepository()
users = UserRepository()
bearer = HTTPBearer(auto_error=False)

# --- Storage ---
storage = LocalFileStorage(settings.storage_dir)


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    """Resolve the canonical user and require verified email for protected routes."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = users.get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


# ---------- Health / Readiness ----------
@app.get("/health")
async def health():
    """Health check — process sống."""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/ready")
async def ready():
    """Readiness check — dependency sẵn sàng."""
    checks = {"database": False, "storage": False}
    try:
        # Test DB connection
        repo.ping()
        checks["database"] = True
    except Exception:
        pass
    try:
        # Test storage writable
        test_path = Path(settings.storage_dir)
        test_path.mkdir(parents=True, exist_ok=True)
        checks["storage"] = test_path.exists()
    except Exception:
        pass
    all_ready = all(checks.values())
    status_code = 200 if all_ready else 503
    return JSONResponse(
        status_code=status_code,
        content={"ready": all_ready, "checks": checks},
    )


# ---------- Auth ----------
# Đăng ký mặc định TẮT khi deploy (ai có link public đều gọi được API).
# Mở bằng env OPEN_REGISTRATION=1 khi cần tạo tài khoản mới.

@app.post("/auth/register", response_model=Token)
async def register(data: UserCreate):
    # Đọc settings tại request time để test monkeypatch có hiệu lực
    _settings = get_settings()
    if not _settings.open_registration:
        raise AppError("REGISTRATION_CLOSED", "Đng ký đang tắt.", status=403)
    if users.get_by_username(data.username):
        raise AppError("USERNAME_EXISTS", "Tên người dùng đã tồn tại.", status=409)
    user = users.create(data.username, hash_password(data.password))
    jti = secrets.token_urlsafe(24)
    refresh_token = create_refresh_jwt(user.id, jti)
    users.store_refresh_token(jti, user.id, (datetime.now(timezone.utc) + timedelta(days=30)).isoformat())
    return {"access_token": create_token(user.id), "refresh_token": refresh_token}


@app.post("/auth/login", response_model=Token)
async def login(data: UserCreate):
    user = users.get_by_username(data.username)
    if not user or not verify_password(data.password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Sai tên người dùng hoặc mật khẩu.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    jti = secrets.token_urlsafe(24)
    refresh_token = create_refresh_jwt(user.id, jti)
    users.store_refresh_token(jti, user.id, (datetime.now(timezone.utc) + timedelta(days=30)).isoformat())
    return {"access_token": create_token(user.id), "refresh_token": refresh_token}


@app.get("/auth/me", response_model=UserPublic)
async def me(user: User = Depends(current_user)):
    return UserPublic(id=user.id, username=user.username)


# ---------- Invoices ----------
def _can_access(invoice: Invoice, user: User) -> bool:
    """Kiểm tra user có quyền truy cập invoice."""
    return invoice.user_id == user.id


@app.get("/invoices", response_model=List[Invoice])
async def list_invoices(
    status: Optional[InvoiceStatus] = None,
    limit: int = 100,
    user: User = Depends(current_user),
):
    return repo.list(user_id=user.id, status=status, limit=limit)


@app.get("/invoices/{invoice_id}", response_model=Invoice)
async def get_invoice(invoice_id: str, user: User = Depends(current_user)):
    invoice = repo.get(invoice_id, user_id=user.id)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    return invoice


@app.post("/invoices", response_model=Invoice)
async def create_invoice(data: InvoiceCreate, user: User = Depends(current_user)):
    invoice = repo.create(data, user_id=user.id)
    return invoice


@app.put("/invoices/{invoice_id}", response_model=Invoice)
async def update_invoice(
    invoice_id: str, data: InvoiceUpdate, user: User = Depends(current_user),
):
    invoice = repo.get(invoice_id, user_id=user.id)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    updated = repo.update(invoice_id, data, user_id=user.id)
    if not updated:
        raise AppError("UPDATE_FAILED", "Cập nhật thất bại.", status=400)
    return updated


@app.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str, user: User = Depends(current_user)):
    invoice = repo.get(invoice_id, user_id=user.id)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    repo.delete(invoice_id, user_id=user.id)
    return {"deleted": True}


# ---------- Upload + Extraction ----------
@app.post("/upload", response_model=Invoice)
async def upload_invoice(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
):
    """Upload file hóa đơn → validate → trích xuất → lưu."""
    content = await file.read()

    # Validate upload
    validated = validate_upload(
        content=content,
        filename=file.filename or "upload",
        declared_mime=file.content_type or "application/octet-stream",
        max_bytes=settings.max_upload_bytes,
        allowed_mimes=settings.allowed_mime_types,
        max_pdf_pages=settings.max_pdf_pages,
    )

    # Lưu file gốc
    storage_key = generate_storage_key(validated.filename, content)
    stored = storage.put(storage_key, content)

    # Trích xuất
    try:
        invoice = extract_invoice(
            content=content,
            mime_type=validated.mime_type,
            source_file=file.filename or "upload",
            user_id=user.id,
        )
    except Exception as e:
        raise AppError(
            "EXTRACTION_FAILED",
            f"Trích xuất thất bại: {str(e)}",
            status=422,
        )

    # Cập nhật provenance với file metadata
    invoice.file_checksum = compute_fingerprint(content)
    invoice.file_size = validated.size
    invoice.page_count = validated.page_count
    invoice.source_file = file.filename or "upload"

    # Lưu invoice
    saved = repo.upsert(invoice)
    return saved


# ---------- Reports ----------
@app.get("/reports/monthly/{period}", response_model=MonthlyReport)
async def monthly_report(period: str, user: User = Depends(current_user)):
    report = repo.monthly_report(period, user_id=user.id)
    if report is None:
        raise AppError("NOT_FOUND", "Không có hóa đơn trong kỳ này.", status=404)
    return report


@app.get("/reports/monthly/{period}/pdf")
async def monthly_report_pdf(period: str, user: User = Depends(current_user)):
    """Xuất báo cáo tháng dạng PDF."""
    report = repo.monthly_report(period, user_id=user.id)
    if report is None:
        raise AppError("NOT_FOUND", "Không có hóa đơn trong kỳ này.", status=404)
    try:
        from .reports.pdf_exporter import export_monthly_pdf
        invoices = repo.list(user_id=user.id)
        pdf_bytes = export_monthly_pdf(report, invoices)
        return Response(content=pdf_bytes, media_type="application/pdf")
    except Exception as e:
        raise AppError("PDF_EXPORT_FAILED", f"Xuất PDF thất bại: {str(e)}", status=500)


# ---------- Static frontend ----------
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
