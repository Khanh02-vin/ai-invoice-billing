"""Payments router — ingest giao dịch ngân hàng/ví -> nhắc upload hóa đơn.

Routes:
- POST /payments/ingest                      -> parse text thô, lưu, tự ghép hóa đơn
- GET  /payments/transactions                -> danh sách (filter status=pending_receipt = notification feed)
- POST /payments/transactions/{id}/dismiss   -> bỏ qua nhắc nhở
- POST /payments/transactions/{id}/match     -> tự ghép tay với hóa đơn

Nguồn: SMS/email bank (VCB/ACB/TCB/MB...), ví MoMo/ZaloPay/VNPay — text thô
từ nguồn nào cũng qua chung parser (src/payments/parser.py).

Dùng current_user_local (copy pattern từ billing.py) để tránh import cycle với app.py.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Optional

import httpx

from fastapi import APIRouter, Body, Depends, Query, UploadFile, File, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, root_validator

from ..auth.security import decode_token
from ..config import get_settings
from ..domain.models import User
from ..domain.payments import PaymentTransaction, TxDirection, TxStatus
from ..errors import AppError
from ..payments.notify import send_receipt_reminder
from ..payments.parser import parse_payment_text
from ..store.storage import generate_storage_key
from ..upload import validate_upload
from ..payments.service import ingest as service_ingest, link
from ..store.users import UserRepository

router = APIRouter(prefix="/payments", tags=["payments"])

_PLACE_CACHE: dict = {}  # key -> (timestamp, places)

bearer = HTTPBearer(auto_error=False)

#懒: resolve qua app module khi request time (billing.py pattern) để test patch được
user_repo: UserRepository | None = None
tx_repo = None  # TransactionRepository
invoice_repo = None  # InvoiceRepository


def _canonical(module_attr: str, fallback_import: str):
    repo = globals()[module_attr]
    if repo is not None:
        return repo
    from .. import app as app_module
    return getattr(app_module, fallback_import)


def current_user_local(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> User:
    """Local copy của current_user để tránh import cycle với app.py."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    repo = _canonical("user_repo", "users")
    user = repo.get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


class IngestRequest(BaseModel):
    raw_text: str = Field(min_length=4, max_length=4000)
    source: str = Field(default="sms", max_length=32)
    external_ref: str = Field(default="", max_length=128)


class MatchRequest(BaseModel):
    invoice_id: str


class SharedPlaceVisitRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="place", max_length=64)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RecordRequest(BaseModel):
    amount: float = Field(gt=0, le=1e15)
    currency: str = Field(default="VND", min_length=3, max_length=3)
    direction: TxDirection
    merchant: str = Field(default="", max_length=120)
    category: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=1000)
    occurred_at: Optional[str] = None
    location_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    location_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    location_captured_at: Optional[str] = None

    @root_validator(skip_on_failure=True)
    def validate_location_pair(cls, values):
        lat, lon = values.get("location_lat"), values.get("location_lon")
        if (lat is None) != (lon is None):
            raise ValueError("Latitude và longitude phải được cung cấp cùng nhau.")
        if lat is None and values.get("location_captured_at"):
            raise ValueError("Không thể lưu thời điểm lấy vị trí khi không có tọa độ.")
        return values


class TransactionDetailsRequest(BaseModel):
    note: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=64)
    location_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    location_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    location_captured_at: Optional[str] = None

    @root_validator(skip_on_failure=True)
    def validate_location_pair(cls, values):
        lat, lon = values.get("location_lat"), values.get("location_lon")
        if (lat is None) != (lon is None):
            raise ValueError("Latitude và longitude phải được cung cấp cùng nhau.")
        if lat is None and values.get("location_captured_at"):
            raise ValueError("Không thể lưu thời điểm lấy vị trí khi không có tọa độ.")
        return values


@router.post("/ingest")
def ingest_event(body: IngestRequest, user: User = Depends(current_user_local)) -> dict:
    """Nhận text thô thông báo giao dịch (SMS/email/wallet push) -> lưu + tự ghép.

    - Đọc được số tiền giao dịch: tạo transaction (pending nếu là debit).
    - Giao dịch debit: chờ hóa đơn nếu chưa ghép được.
    - Giao dịch credit: lưu vào sổ thu nhập.
    - Trùng nội dung (retry/forward lại): trả về transaction đã có.
    - Không đọc được số tiền: 422 UNPARSEABLE.
    """
    event = parse_payment_text(body.raw_text, source=body.source)
    if event is None:
        raise AppError(
            "UNPARSEABLE",
            "Không đọc được số tiền giao dịch trong nội dung.",
            status=422,
        )
    if event.direction == TxDirection.CREDIT:
        tx, _, duplicate = service_ingest(
            _canonical("tx_repo", "tx_repo"), _canonical("invoice_repo", "repo"),
            user.id, event, body.raw_text, external_ref=body.external_ref,
        )
        return {"stored": not duplicate, "duplicate": duplicate, "transaction": tx, "matched_invoice_id": ""}

    tx, invoice, duplicate = service_ingest(
        _canonical("tx_repo", "tx_repo"),
        _canonical("invoice_repo", "repo"),
        user.id,
        event,
        body.raw_text,
        external_ref=body.external_ref,
    )
    # Chưa ghép được hóa đơn -> nhắc chụp bill qua email (fire-and-forget,
    # SMTP không chặn response; không cấu hình SMTP thì bỏ qua im lặng)
    if not duplicate and invoice is None and tx.status == TxStatus.PENDING_RECEIPT:
        threading.Thread(
            target=send_receipt_reminder, args=(user, tx), daemon=True
        ).start()
    return {
        "stored": not duplicate,
        "duplicate": duplicate,
        "transaction": tx,
        "matched_invoice_id": invoice.id if invoice else "",
    }


@router.post("/transactions")
def record_transaction(body: RecordRequest, user: User = Depends(current_user_local)) -> PaymentTransaction:
    """Ghi khoản thu/chi thủ công, tùy chọn kèm tọa độ được người dùng chấp thuận."""
    ref = "manual:" + uuid.uuid4().hex
    repo = _canonical("tx_repo", "tx_repo")
    tx = repo.create(PaymentTransaction(
        user_id=user.id, amount=body.amount, currency=body.currency.upper(),
        direction=body.direction, merchant=body.merchant.strip(),
        occurred_at=body.occurred_at or datetime.utcnow().isoformat(), source="manual",
        external_ref=ref, status=TxStatus.RECORDED, note=body.note.strip(),
        category=body.category.strip(), location_lat=body.location_lat,
        location_lon=body.location_lon,
        location_captured_at=body.location_captured_at,
    ))
    return tx


@router.get("/places")
def suggest_places(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), radius: int = 1500, user: User = Depends(current_user_local)) -> dict:
    """Tìm POI lân cận qua Overpass; không gửi thông tin giao dịch."""
    import httpx
    radius = max(100, min(radius, 5000))
    cache_key = (lat, lon, radius)
    cached = _PLACE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 300:
        places = cached[1]
    else:
        query = f"[out:json][timeout:10];(node(around:{radius},{lat},{lon})[amenity];node(around:{radius},{lat},{lon})[shop];node(around:{radius},{lat},{lon})[tourism];);out center 30;"
        try:
            response = httpx.post("https://overpass-api.de/api/interpreter", data={"data": query}, timeout=12)
            response.raise_for_status()
            elements = response.json().get("elements", [])
            places = [{
                "id": str(item.get("id")), "name": item.get("tags", {}).get("name", ""),
                "kind": next((item["tags"][key] for key in ("amenity", "shop", "tourism") if key in item.get("tags", {})), "place"),
                "lat": item.get("lat") or item.get("center", {}).get("lat"),
                "lon": item.get("lon") or item.get("center", {}).get("lon"),
            } for item in elements if item.get("tags", {}).get("name")]
        except Exception:
            if cached:
                places = cached[1]  # fallback to stale cache when Overpass rate-limited down
            else:
                raise AppError("PLACES_UNAVAILABLE", "Không lấy được địa điểm lân cận lúc này.", status=503)
        _PLACE_CACHE[cache_key] = (time.time(), places)
    return {"places": places}


@router.post("/transactions/{tx_id}/attachments")
async def upload_transaction_attachment(
    tx_id: str, file: UploadFile = File(...), user: User = Depends(current_user_local)
) -> dict:
    repo = _canonical("tx_repo", "tx_repo")
    if not repo.get(tx_id, user_id=user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    validated = validate_upload(
        content, file.filename or "receipt", file.content_type or "application/octet-stream",
        settings.max_upload_bytes, settings.allowed_mime_types, settings.max_pdf_pages,
    )
    from .. import app as app_module
    storage = app_module.storage
    key = generate_storage_key(file.filename or "receipt", validated.content)
    stored = storage.put(key, validated.content)
    attachment_id = uuid.uuid4().hex
    try:
        attachment = repo.add_attachment(attachment_id, tx_id, user.id, stored.key,
                                         validated.mime_type, validated.size)
    except Exception:
        storage.delete(stored.key)
        raise
    return attachment


@router.get("/transactions/{tx_id}/attachments")
def list_transaction_attachments(tx_id: str, user: User = Depends(current_user_local)) -> list[dict]:
    repo = _canonical("tx_repo", "tx_repo")
    if not repo.get(tx_id, user_id=user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    return repo.list_attachments(tx_id, user.id)


@router.get("/attachments/{attachment_id}/content")
def get_transaction_attachment(attachment_id: str, user: User = Depends(current_user_local)) -> Response:
    repo = _canonical("tx_repo", "tx_repo")
    attachment = repo.get_attachment(attachment_id, user.id)
    if not attachment:
        raise AppError("NOT_FOUND", "Không tìm thấy chứng từ.", status=404)
    from .. import app as app_module
    content = app_module.storage.get(attachment["storage_key"])
    if content is None:
        raise AppError("NOT_FOUND", "Không tìm thấy file chứng từ.", status=404)
    return Response(content, media_type=attachment["mime_type"], headers={"Content-Disposition": "inline"})


@router.delete("/attachments/{attachment_id}")
def delete_transaction_attachment(attachment_id: str, user: User = Depends(current_user_local)) -> dict:
    repo = _canonical("tx_repo", "tx_repo")
    attachment = repo.delete_attachment(attachment_id, user.id)
    if not attachment:
        raise AppError("NOT_FOUND", "Không tìm thấy chứng từ.", status=404)
    from .. import app as app_module
    app_module.storage.delete(attachment["storage_key"])
    return {"deleted": True}


@router.put("/preferences/place-sharing")
def set_place_sharing(enabled: bool, user: User = Depends(current_user_local)) -> dict:
    repo = _canonical("user_repo", "users")
    repo.set_place_sharing(user.id, enabled)
    return {"share_place_visits": repo.get_place_sharing(user.id)}


@router.get("/preferences/place-sharing")
def get_place_sharing(user: User = Depends(current_user_local)) -> dict:
    return {"share_place_visits": _canonical("user_repo", "users").get_place_sharing(user.id)}


@router.post("/places/share")
def share_place_visit(body: SharedPlaceVisitRequest, user: User = Depends(current_user_local)) -> dict:
    users = _canonical("user_repo", "users")
    if not users.get_place_sharing(user.id):
        raise AppError("SHARING_DISABLED", "Bật chia sẻ địa điểm trước khi gửi lượt ghé.", status=403)
    _canonical("tx_repo", "tx_repo").add_shared_place_visit(
        user.id, body.name.strip(), body.kind.strip() or "place", body.lat, body.lon,
    )
    return {"shared": True}


@router.get("/places/shared")
def shared_places(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                  radius: int = 1500, user: User = Depends(current_user_local)) -> dict:
    radius = max(100, min(radius, 5000))
    users = _canonical("user_repo", "users")
    places = _canonical("tx_repo", "tx_repo").list_shared_places(
        lat, lon, radius, users.list_place_sharing_ids(), exclude_user_id=user.id,
    )
    return {"places": places}


@router.put("/transactions/{tx_id}")
def update_transaction(tx_id: str, body: TransactionDetailsRequest, user: User = Depends(current_user_local)) -> PaymentTransaction:
    repo = _canonical("tx_repo", "tx_repo")
    tx = repo.get(tx_id, user_id=user.id)
    if not tx:
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    repo.update_details(tx_id, user.id, note=body.note.strip(), category=body.category.strip(),
                        location_lat=body.location_lat, location_lon=body.location_lon,
                        location_captured_at=body.location_captured_at)
    return repo.get(tx_id, user_id=user.id)


@router.get("/transactions")
def list_transactions(
    status: Optional[TxStatus] = None,
    direction: Optional[TxDirection] = None,
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    limit: int = 50,
    user: User = Depends(current_user_local),
) -> list[PaymentTransaction]:
    """Danh sách giao dịch của user.

    GET ?status=pending_receipt -> feed "chờ upload hóa đơn" (notification feed).
    """
    limit = max(1, min(limit, 200))
    if start_date and end_date and start_date > end_date:
        raise AppError("INVALID_DATE_RANGE", "Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.", status=422)
    return _canonical("tx_repo", "tx_repo").list(
        user_id=user.id, status=status, direction=direction, start_date=start_date,
        end_date=end_date, limit=limit
    )


@router.post("/transactions/{tx_id}/dismiss")
def dismiss_transaction(tx_id: str, user: User = Depends(current_user_local)) -> dict:
    """Bỏ qua nhắc nhở (không cần hóa đơn cho giao dịch này)."""
    repo = _canonical("tx_repo", "tx_repo")
    tx = repo.get(tx_id, user_id=user.id)
    if not tx:
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    repo.set_status(tx_id, TxStatus.DISMISSED, user_id=user.id)
    return {"dismissed": True, "transaction": repo.get(tx_id, user_id=user.id)}


@router.post("/transactions/{tx_id}/match")
def manual_match(tx_id: str, body: MatchRequest, user: User = Depends(current_user_local)) -> dict:
    """Ghép tay giao dịch với hóa đơn (user tự biết 2 cái cùng nhau)."""
    tx_repo_local = _canonical("tx_repo", "tx_repo")
    tx = tx_repo_local.get(tx_id, user_id=user.id)
    if not tx:
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    invoice = _canonical("invoice_repo", "repo").get(body.invoice_id, user_id=user.id)
    if not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy hóa đơn.", status=404)
    link(tx_repo_local, _canonical("invoice_repo", "repo"), tx, invoice)
    return {"matched": True, "transaction": tx_repo_local.get(tx_id, user_id=user.id)}
