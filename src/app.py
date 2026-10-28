"""FastAPI app cho Invoice & Billing System. Auth JWT + đa người dùng.

Nguyên tắc (PLAN mục 3):
- CORS allowlist; security headers; request ID; structured logging.
- Error contract JSON ổn định; không trả stack trace/PII.
- Upload validation: dung lượng, MIME sniff, số trang.
- Health/readiness phân biệt process sống và dependency sẵn sàng.
"""

import secrets
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import List, NamedTuple, Optional

import logging

logger = logging.getLogger("invoice.app")

from fastapi import Depends, FastAPI, File, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

bearer = HTTPBearer(auto_error=False)

from .auth.security import create_refresh_token as create_refresh_jwt
from .auth.security import create_token, decode_token, hash_password, verify_password
from .config import get_settings
from .domain.models import (
    FieldCorrection,
    InvoiceCreate,
    InvoiceStatus,
    InvoiceUpdate,
    JobStatus,
    MonthlyReport,
    Token,
    UserCreate,
    UserPublic,
)
from .middleware import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    StructuredLoggingMiddleware,
    configure_cors,
)
from .errors import AppError, register_error_handlers, _public_body
from .models import Invoice, TxDirection, TxStatus, User
from .observability import metrics as obs_metrics
from .routers.auth_ext import router as auth_ext_router
from .routers.auth_ext import send_verification_email
from .routers.api_keys import router as api_keys_router
from .routers.organizations import router as organizations_router
from .routers.payments import router as payments_router
from .routers.push import router as push_router
from .routers.search import router as search_router
from .payments.service import match_invoice_saved as _match_invoice_saved
from .security.idempotency import InMemoryCache, compute_content_hash, generate_idempotency_key
from .security.rate_limit import RateLimiter  # noqa: F401  (kept for backward import)
from .security.api_rate_limit import (
    RateLimitExceeded,
    rate_limit_dependency,
)
from .store.sql_repository import (
    ApiKeyRepository,
    DuplicateInvoiceError,
    InvoiceRepository,
    InvoiceTransactionLinkRepository,
    OrganizationRepository,
    PushSubscriptionRepository,
    TransactionRepository,
    UserRepository,
    init_database,
)
from .store.repository import DuplicateInvoiceError as LegacyDuplicateInvoiceError
from .store.storage import LocalFileStorage, compute_fingerprint, generate_storage_key
from .upload import validate_upload

settings = get_settings()
settings.ensure_production_readiness()
_upload_idempotency = InMemoryCache()
storage = LocalFileStorage(settings.storage_dir)


def match_invoice_saved(tx_repo, invoice_repo, invoice: Invoice):
    """Match saved invoices when a payment transaction repository is available."""
    try:
        return _match_invoice_saved(tx_repo, invoice_repo, invoice)
    except Exception as exc:
        if "payment_transactions" not in str(exc):
            raise
        return None

app = FastAPI(title="Invoice & Billing System", version="3.0.0")

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

# --- Routers (Phase 2-4) ---
app.include_router(search_router)
app.include_router(auth_ext_router)
app.include_router(push_router)
app.include_router(payments_router)
app.include_router(api_keys_router)
app.include_router(organizations_router)

# --- IMAP poller (tùy chọn): đọc email báo giao dịch bank/ví -> /payments/ingest ---
# Chỉ chạy khi cấu hình IMAP_HOST + IMAP_USER; app vẫn boot bình thường khi thiếu.
from .payments.imap_poller import ImapPoller

_imap_poller = ImapPoller()


@app.on_event("startup")
async def _start_imap_poller():
    _imap_poller.start()


@app.on_event("shutdown")
async def _stop_imap_poller():
    _imap_poller.stop()


# --- Initialize Database ---
db = init_database(settings.database_path)
repo = InvoiceRepository(db)
users = UserRepository(db)
tx_repo = TransactionRepository(db)
push_repo = PushSubscriptionRepository(db)
api_keys_repo = ApiKeyRepository(db)
orgs_repo = OrganizationRepository(db)


# --- Observability (Phase 4) ---
@app.get("/metrics")
async def metrics_endpoint():
    return obs_metrics.metrics_response()


def current_user(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)
) -> User:
    """Resolve user từ JWT bearer, hoặc X-API-Key cho bên thứ ba (có scope).

    X-API-Key có mặt -> xác thực qua src/security/api_keys.py (scope theo
    method+path, rate limit per key); key gắn với đúng 1 user. Route ngoài
    SCOPE_RULES bị chặn 403 với API key, JWT không bị ảnh hưởng.
    """
    from .security.api_keys import user_from_api_key

    api_key_user = user_from_api_key(request, users, api_keys_repo)
    if api_key_user is not None:
        return api_key_user

    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = users.get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


class OrgContext(NamedTuple):
    """Ngữ cảnh org của request (X-Organization-Id)."""

    id: str
    role: str

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "admin", "member")


def current_organization(request: Request, user: User = Depends(current_user)) -> OrgContext | None:
    """Org context của request; None = ngữ cảnh cá nhân (spec multi-tenant mục 4).

    Header X-Organization-Id có mặt -> kiểm org tồn tại (404) + membership (403).
    """
    org_id = request.headers.get("X-Organization-Id")
    if not org_id:
        return None
    if not orgs_repo.get(org_id):
        raise AppError("NOT_FOUND", "Không tìm thấy tổ chức.", status=404)
    membership = orgs_repo.get_membership(org_id, user.id)
    if not membership:
        raise AppError("FORBIDDEN", "Bạn không phải thành viên tổ chức này.", status=403)
    return OrgContext(id=org_id, role=membership.role)


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
    return JSONResponse(status_code=status_code, content={"ready": all_ready, "checks": checks})


# ---------- Auth ----------
# Đăng ký mặc định TẮT khi deploy (ai có link public đều gọi được API).
# Mở bằng env OPEN_REGISTRATION=1 khi cần tạo tài khoản mới.


@app.post("/auth/register", response_model=Token)
async def register(
    data: UserCreate,
    request: Request,
    _rl: None = Depends(rate_limit_dependency("rate_limit_auth_register")),
):
    # Đọc settings tại request time để test monkeypatch có hiệu lực
    _settings = get_settings()
    if not _settings.open_registration:
        raise AppError("REGISTRATION_CLOSED", "Đng ký đang tắt.", status=403)
    if users.get_by_username(data.username):
        raise AppError("USERNAME_EXISTS", "Tên người dùng đã tồn tại.", status=409)
    if data.email and "@" not in data.email:
        raise AppError("INVALID_EMAIL", "Email không hợp lệ.", status=400)
    user = users.create(data.username, hash_password(data.password), email=data.email or "")
    jti = secrets.token_urlsafe(24)
    refresh_token = create_refresh_jwt(user.id, jti)
    users.store_refresh_token(jti, user.id, (datetime.now(UTC) + timedelta(days=30)).isoformat())
    # Gửi email xác minh ngay (best-effort): SMTP chưa cấu hình hoặc user không có
    # email -> verification_sent=False; user gọi /auth/resend-verification sau khi
    # quản trị viên cấu hình SMTP.
    verification_sent = False
    if user.email:
        base_url = _settings.app_url or str(request.base_url).rstrip("/")
        verification_sent = send_verification_email(user, users, base_url=base_url)
    return {
        "access_token": create_token(user.id),
        "refresh_token": refresh_token,
        "verification_sent": verification_sent,
    }


@app.post("/auth/login", response_model=Token)
async def login(
    data: UserCreate,
    _rl: None = Depends(rate_limit_dependency("rate_limit_auth_login")),
):
    user = users.get_by_username(data.username)
    if not user or not verify_password(data.password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Sai tên người dùng hoặc mật khẩu.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    jti = secrets.token_urlsafe(24)
    refresh_token = create_refresh_jwt(user.id, jti)
    users.store_refresh_token(jti, user.id, (datetime.now(UTC) + timedelta(days=30)).isoformat())
    return {"access_token": create_token(user.id), "refresh_token": refresh_token}


@app.get("/auth/me", response_model=UserPublic)
async def me(user: User = Depends(current_user)):
    return UserPublic(id=user.id, username=user.username, email=user.email)


# ---------- Invoices ----------
def _can_access(invoice: Invoice, user: User) -> bool:
    """Kiểm tra user có quyền truy cập invoice."""
    return invoice.user_id == user.id


@app.get("/invoices", response_model=List[Invoice])
async def list_invoices(
    status: InvoiceStatus | None = None,
    limit: int = 100,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    return repo.list(
        user_id=user.id, status=status, limit=limit,
        organization_id=org.id if org else None,
    )


@app.get("/invoices/{invoice_id}", response_model=Invoice)
async def get_invoice(
    invoice_id: str,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    invoice = repo.get(invoice_id, user_id=user.id, organization_id=org.id if org else None)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    return invoice


@app.post("/invoices", response_model=Invoice)
async def create_invoice(
    data: InvoiceCreate,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    if org and not org.can_write:
        raise AppError("FORBIDDEN", "Role viewer chỉ được xem.", status=403)
    try:
        invoice = repo.create(data, user_id=user.id, organization_id=org.id if org else None)
    except (DuplicateInvoiceError, LegacyDuplicateInvoiceError) as e:
        raise AppError(
            "DUPLICATE_INVOICE",
            f"Hóa đơn số {e.invoice_number} đã tồn tại.",
            status=409,
            details={"existing_id": e.existing_id},
        )
    # Giao dịch đã đến trước? Ghép ngay nếu số tiền khớp — reload để response
    # phản ánh trạng thái PAID sau khi link (object create là bản chưa ghép).
    matched_tx = match_invoice_saved(tx_repo, repo, invoice)
    if matched_tx:
        invoice = repo.get(invoice.id, user_id=user.id, organization_id=org.id if org else None)
    return invoice


@app.put("/invoices/{invoice_id}")
async def update_invoice(
    invoice_id: str,
    data: InvoiceUpdate,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    if org and not org.can_write:
        raise AppError("FORBIDDEN", "Role viewer chỉ được xem.", status=403)
    invoice = repo.get(invoice_id, user_id=user.id, organization_id=org.id if org else None)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    updated = repo.update(
        invoice_id, data, user_id=user.id, organization_id=org.id if org else None
    )
    if not updated:
        raise AppError("UPDATE_FAILED", "Cập nhật thất bại.", status=400)
    matched_tx = None
    if updated.status not in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED):
        matched_tx = match_invoice_saved(tx_repo, repo, updated)
    result = updated.model_dump(mode="json")
    result["payment_match_status"] = "matched" if matched_tx else "unmatched"
    result["matched_transaction_id"] = matched_tx.id if matched_tx else None
    return result


@app.delete("/invoices/{invoice_id}")
async def delete_invoice(
    invoice_id: str,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    if org and not org.can_write:
        raise AppError("FORBIDDEN", "Role viewer chỉ được xem.", status=403)
    invoice = repo.get(invoice_id, user_id=user.id, organization_id=org.id if org else None)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    repo.delete(invoice_id, user_id=user.id, organization_id=org.id if org else None)
    return {"deleted": True}


# ---------- Review: user sửa field máy đọc sai → data eval ----------
@app.get("/invoices/{invoice_id}/corrections", response_model=List[FieldCorrection])
async def list_corrections(
    invoice_id: str,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    """Lịch sử sửa field của 1 hóa đơn (old→new + provenance lúc máy đọc)."""
    invoice = repo.get(invoice_id, user_id=user.id, organization_id=org.id if org else None)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    return [FieldCorrection(**c) for c in invoice.review_history]


@app.get("/eval/corrections", response_model=List[FieldCorrection])
async def export_corrections(
    limit: int = 500,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    """Export toàn bộ corrections của user — data eval cho benchmark.

    Mỗi dòng: field, old_value (máy đọc), new_value (user sửa = ground truth),
    confidence/source lúc máy đọc → đo được ngưỡng nào là đáng ngờ.
    """
    out: List[FieldCorrection] = []
    for inv in repo.list(
        user_id=user.id, limit=1000, organization_id=org.id if org else None
    ):
        for c in inv.review_history:
            out.append(
                FieldCorrection(
                    invoice_id=inv.id,
                    field=c.get("field", ""),
                    old_value=c.get("old_value"),
                    new_value=c.get("new_value"),
                    confidence=c.get("confidence"),
                    source=c.get("source"),
                    corrected_by=c.get("corrected_by", ""),
                    corrected_at=c.get("corrected_at", ""),
                )
            )
    out.sort(key=lambda c: c.corrected_at or "", reverse=True)
    return out[: max(1, min(limit, 5000))]


# ---------- Upload + Extraction ----------
def _upload_result(inv: Invoice, transaction_id: str | None) -> dict:
    """Payload /upload: invoice dump + trạng thái job + trạng thái đối soát.

    job chưa chạy/xong đang chạy -> "processing"; lỗi extraction -> "failed";
    extraction xong -> matched/amount_mismatch/unmatched theo link trong DB.
    """
    result = inv.model_dump(mode="json")
    result["job_status"] = inv.job_status.value
    if inv.job_status in (JobStatus.PENDING, JobStatus.PROCESSING):
        result["payment_match_status"], result["matched_transaction_id"] = "processing", None
    elif inv.job_status == JobStatus.FAILED:
        result["payment_match_status"], result["matched_transaction_id"] = "failed", None
    elif transaction_id:
        tx = tx_repo.get(transaction_id, inv.user_id)
        if tx and tx.matched_invoice_id == inv.id:
            result["payment_match_status"], result["matched_transaction_id"] = "matched", tx.id
        else:
            result["payment_match_status"], result["matched_transaction_id"] = (
                "amount_mismatch",
                None,
            )
    else:
        # Link stamp theo user của transaction — org context tra không lọc user
        links = InvoiceTransactionLinkRepository(tx_repo.db).list_for_invoice(
            inv.id, "" if inv.organization_id else inv.user_id
        )
        if links:
            result["payment_match_status"], result["matched_transaction_id"] = (
                "matched",
                links[0].transaction_id,
            )
        else:
            result["payment_match_status"], result["matched_transaction_id"] = "unmatched", None
    return result


@app.post("/upload")
async def upload_invoice(
    file: UploadFile = File(...),
    request: Request = None,
    transaction_id: str | None = None,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    """Upload file hóa đơn → validate → trích xuất → lưu."""
    if org and not org.can_write:
        raise AppError("FORBIDDEN", "Role viewer chỉ được xem.", status=403)
    org_id = org.id if org else None
    content = await file.read()

    if transaction_id:
        pending = tx_repo.get(transaction_id, user.id, organization_id=org_id)
        if not pending:
            raise AppError("TRANSACTION_NOT_PENDING", "Không tìm thấy giao dịch đã chọn.", status=409)

    # Idempotency: explicit key is preferred; content-derived fallback protects retries.
    idem_key = request.headers.get("Idempotency-Key") if request else None
    if not idem_key:
        idem_key = generate_idempotency_key(
            user.id, file.filename or "upload", len(content), compute_content_hash(content)
        )
    if transaction_id:
        idem_key = f"{idem_key}:{transaction_id}"
    if org:
        # Cùng file upload vào org khác nhau là 2 hóa đơn riêng
        idem_key = f"{idem_key}:org:{org_id}"
    cached = _upload_idempotency.get(idem_key)
    if cached:
        invoice_id = cached.get("id")
        inv = repo.get(invoice_id, user_id=user.id, organization_id=org_id) if invoice_id else None
        if inv is not None:
            # Trả trạng thái hiện tại trong DB (replay cũ có thể nói "processing"
            # trong khi worker đã xong).
            return JSONResponse(
                content=_upload_result(inv, transaction_id),
                status_code=200,
                headers={"X-Idempotent-Replay": "true"},
            )

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
    storage_dir = os.getenv("STORAGE_DIR", "data/storage")
    storage = LocalFileStorage(storage_dir)
    print(f"DEBUG upload: storage_dir={storage_dir!r}, storage_key={storage_key}")
    stored = storage.put(storage_key, content)

    # Tạo bản ghi hóa đơn ở trạng thái đang xử lý (nội dung chưa được trích xuất).
    invoice = Invoice(
        invoice_number="unknown",
        vendor="unknown",
        buyer="",
        issue_date=None,
        due_date=None,
        currency="VND",
        subtotal=0.0,
        total=0.0,
        tax=0.0,
        tax_rate=0.0,
        discount=0.0,
        amount_due=0.0,
        status=InvoiceStatus.UNPAID,
        source_file=storage_key,
        raw_snippet="",
        confidence=0.0,
        user_id=user.id,
        job_status=JobStatus.PROCESSING,
        file_checksum=compute_fingerprint(content),
        file_size=validated.size,
        page_count=validated.page_count,
        extraction_provider="",
        review_history=[],
        organization_id=org_id,
    )
    try:
        saved = repo.upsert(invoice)
    except (DuplicateInvoiceError, LegacyDuplicateInvoiceError) as e:
        storage.delete(stored.key)
        raise AppError(
            "DUPLICATE_INVOICE",
            f"Hóa đơn số {e.invoice_number} đã tồn tại.",
            status=409,
            details={"existing_id": e.existing_id},
        )

    # Trích xuất: WORKER_ENABLED=1 -> đẩy task Celery và trả 202 "processing"
    # ngay (client polling /invoices/{id}); không thì chạy đồng bộ tại đây và
    # trả trạng thái thật (fallback cho dev/test/API-only không cần broker).
    enqueued = False
    if get_settings().worker_enabled:
        try:
            from .workers.extraction_worker import extract_invoice

            extract_invoice.delay(
                saved.id, storage_key, user.id, transaction_id or "", org_id
            )
            enqueued = True
        except Exception:
            logger.warning("upload: không enqueue được Celery task, chuyển sang chạy đồng bộ")

    if not enqueued:
        try:
            from .workers.extraction_worker import _do_extract

            _do_extract(
                saved.id, storage_key, user.id,
                invoice_repo=repo, tx_repo=tx_repo,
                db_path=settings.database_path, storage_dir=storage_dir,
                transaction_id=transaction_id or "",
                organization_id=org_id,
            )
        except Exception:
            # _do_extract đã tự đánh dấu FAILED; trả trạng thái thật bên dưới.
            logger.exception("upload: extraction đồng bộ thất bại cho %s", saved.id)

    result = _upload_result(
        repo.get(saved.id, user_id=user.id, organization_id=org_id) or saved, transaction_id
    )
    _upload_idempotency.set(idem_key, result)
    return JSONResponse(content=result, status_code=202)


# ---------- Reports ----------
@app.get("/reports/monthly/{period}", response_model=MonthlyReport)
async def monthly_report(
    period: str,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    report = repo.monthly_report(period, user_id=user.id, organization_id=org.id if org else None)
    if report is None:
        raise AppError("NOT_FOUND", "Không có hóa đơn trong kỳ này.", status=404)
    return report


@app.get("/reports/monthly/{period}/pdf")
async def monthly_report_pdf(
    period: str,
    user: User = Depends(current_user),
    org: OrgContext | None = Depends(current_organization),
):
    """Xuất báo cáo tháng dạng PDF."""
    org_id = org.id if org else None
    report = repo.monthly_report(period, user_id=user.id, organization_id=org_id)
    if report is None:
        raise AppError("NOT_FOUND", "Không có hóa đơn trong kỳ này.", status=404)
    try:
        from .reports.pdf_exporter import export_monthly_pdf

        invoices = repo.list(user_id=user.id, organization_id=org_id)
        pdf_bytes = export_monthly_pdf(report, invoices)
        return Response(content=pdf_bytes, media_type="application/pdf")
    except Exception as e:
        raise AppError("PDF_EXPORT_FAILED", f"Xuất PDF thất bại: {e!s}", status=500)


# ---------- Static frontend ----------
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
