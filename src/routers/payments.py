"""Payments router — timeline, place suggestions, email connect, camera upload.

Phase 1: New endpoints for dashboard redesign.
- GET /payments/timeline -> journey map data (transactions with location, ordered by time)
- GET /payments/places/suggestions -> AI place recommendations
- GET /payments/email-status -> Check IMAP configuration status
- POST /payments/email-connect -> IMAP configuration for auto bill ingestion
- POST /payments/camera-upload -> Camera capture + Vision OCR
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone
from math import isfinite

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator
from sqlmodel import select

from ..auth.security import decode_token
from ..config import get_settings
from ..errors import AppError
from ..extract.extractor import _extract_text, extract_invoice
from ..llm.base import get_llm_provider
from ..models import (
    PaymentTransaction,
    SharedPlaceVisit,
    TxDirection,
    TxStatus,
    User,
    UserEmailAccount,
    UserPaymentPreference,
)
from ..security.api_rate_limit import rate_limit_dependency
from ..store.sql_repository import (
    Database,
    InvoiceRepository,
    InvoiceTransactionLinkRepository,
    TransactionRepository,
    UserRepository,
    get_database,
)
from ..store.storage import LocalFileStorage, compute_fingerprint, generate_storage_key
from ..upload import validate_upload

router = APIRouter(prefix="/payments", tags=["payments"])
bearer = HTTPBearer(auto_error=False)

# Lazy DB
_db: Database | None = None


def _app_module():
    from .. import app as app_module

    return app_module


def _get_db() -> Database:
    global _db
    try:
        from .. import app as app_module

        app_db = getattr(getattr(app_module, "users", None), "db", None)
        if app_db is not None:
            return app_db
    except Exception:
        pass
    if _db is None:
        _db = get_database(get_settings().database_path)
    return _db


def current_user(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)
) -> User:
    # X-API-Key (bên thứ ba, scope payments:read/write) — chung helper với app.py
    from ..security.api_keys import user_from_api_key

    try:
        api_repo = getattr(_app_module(), "api_keys_repo", None)
        app_users = getattr(_app_module(), "users", None)
        if api_repo is not None and app_users is not None:
            api_key_user = user_from_api_key(request, app_users, api_repo)
            if api_key_user is not None:
                return api_key_user
    except AppError:
        raise
    except Exception:
        pass

    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    try:
        app_users = getattr(_app_module(), "users", None)
        if app_users is not None and hasattr(app_users, "get"):
            user = app_users.get(user_id)
            if user:
                return user
    except Exception:
        pass
    db = _get_db()
    user = db.get_session().get(User, user_id)
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    if not user.verified:
        raise AppError("EMAIL_NOT_VERIFIED", "Vui lòng xác minh email trước.", status=403)
    return user


# ---- Request/Response Models ----


class EmailStatusResponse(BaseModel):
    connected: bool
    config: dict | None = None


class TimelinePoint(BaseModel):
    """A point on the journey timeline."""

    id: str
    type: str  # "transaction" | "place"
    lat: float
    lon: float
    name: str
    address: str = ""
    amount: float = 0
    currency: str = "VND"
    merchant: str = ""
    occurred_at: str
    sequence: int
    transaction_id: str = ""
    invoice_id: str = ""
    transport_mode: str = ""  # "walk", "bike", "car", "grab", "bus"


class TimelineResponse(BaseModel):
    points: list[TimelinePoint]
    bounds: dict | None = None  # {min_lat, max_lat, min_lon, max_lon}
    total_distance_km: float = 0
    total_spent: float = 0
    currency: str = "VND"


class PlaceSuggestion(BaseModel):
    """AI-powered place suggestion."""

    name: str
    kind: str  # "cafe", "restaurant", "shop", etc.
    lat: float
    lon: float
    distance_m: int
    reason: str  # "tương đồng thói quen", "đã ghé 3 lần", "gần bạn"
    confidence: float
    source: str  # "shared" | "history" | "nearby"
    visit_count: int = 0


class PlaceSuggestionsResponse(BaseModel):
    suggestions: list[PlaceSuggestion]


class EmailConnectRequest(BaseModel):
    """IMAP configuration for email bill ingestion."""

    host: str = Field(..., min_length=1)
    port: int = Field(993, ge=1, le=65535)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    folder: str = "INBOX"
    interval_seconds: int = Field(60, ge=10, le=3600)
    user_map: str = ""  # "email1@x.com=username1,email2@y.com=username2"


class EmailConnectResponse(BaseModel):
    ok: bool
    message: str
    test_result: dict | None = None


class CameraUploadResponse(BaseModel):
    invoice_id: str
    message: str
    needs_review: bool = False


class TransactionCreate(BaseModel):
    amount: float = Field(gt=0, le=1_000_000_000_000)
    currency: str = Field(default="VND", min_length=3, max_length=3)
    direction: TxDirection = TxDirection.DEBIT
    merchant: str = Field(default="", max_length=120)
    occurred_at: str | None = None
    note: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=64)
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)
    location_captured_at: str | None = None

    @model_validator(mode="after")
    def location_pair(self):
        if (self.location_lat is None) != (self.location_lon is None):
            raise ValueError("Both coordinates must be supplied together")
        if not isfinite(self.amount):
            raise ValueError("Amount must be finite")
        return self


class TransactionUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0, le=1_000_000_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    direction: TxDirection | None = None
    merchant: str | None = Field(default=None, max_length=120)
    occurred_at: str | None = None
    note: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=64)
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)
    location_captured_at: str | None = None

    @model_validator(mode="after")
    def location_pair(self):
        if (self.location_lat is None) != (self.location_lon is None):
            raise ValueError("Both coordinates must be supplied together")
        return self


class PlaceShareRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="place", max_length=64)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    confirmed: bool = True


class PlaceSharingPreference(BaseModel):
    share_place_visits: bool


class InvoiceLinkRequest(BaseModel):
    invoice_id: str
    amount: float | None = Field(default=None, gt=0)


_ALLOWED_IMAP_HOSTS = {"imap.gmail.com", "outlook.office365.com", "imap.mail.yahoo.com"}


def _parse_timeline_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise AppError("INVALID_DATE", "Ngày không hợp lệ.", status=400)


def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import atan2, cos, radians, sin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * atan2(sqrt(a), sqrt(1 - a))


def _fernet() -> Fernet:
    key = os.getenv("IMAP_CREDENTIAL_KEY", "")
    if not key:
        raise AppError("EMAIL_ENCRYPTION_UNAVAILABLE", "Lưu thông tin email chưa được cấu hình.", status=503)
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise AppError("EMAIL_ENCRYPTION_UNAVAILABLE", "Khóa mã hóa email không hợp lệ.", status=503)


@router.get("/transactions")
async def list_transactions(
    direction: TxDirection | None = None,
    status: TxStatus | None = None,
    limit: int = Query(100, ge=1, le=200),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    user: User = Depends(current_user),
):
    if from_date and to_date and from_date > to_date:
        raise AppError("INVALID_DATE_RANGE", "Khoảng ngày không hợp lệ.", status=400)
    return TransactionRepository(_get_db()).list(
        user_id=user.id, direction=direction, status=status, limit=limit,
        start_date=from_date, end_date=to_date,
    )


@router.post("/transactions")
async def create_transaction(
    body: TransactionCreate,
    user: User = Depends(current_user),
    _rl: None = Depends(rate_limit_dependency("rate_limit_upload")),
):
    tx_repo = TransactionRepository(_get_db())
    tx = PaymentTransaction(user_id=user.id, **body.model_dump())
    tx = tx_repo.create(tx)
    match_status = "unmatched"
    if tx.direction == TxDirection.DEBIT and tx.status == TxStatus.PENDING_RECEIPT:
        from ..payments.matching import pick_invoices_decision
        from ..payments.service import link, link_many

        invoice_repo = InvoiceRepository(_get_db())
        decision = pick_invoices_decision(
            tx, invoice_repo.list(user_id=user.id, limit=200)
        )
        if decision.kind == "unique":
            invoices = decision.invoices
            if len(invoices) > 1:
                link_many(tx_repo, invoice_repo, tx, invoices)
                match_status = "matched"
            else:
                link(tx_repo, invoice_repo, tx, invoices[0])
                match_status = "matched"
        elif decision.kind == "ambiguous":
            match_status = "ambiguous"
    result = tx.model_dump(mode="json")
    result["payment_match_status"] = match_status
    return result


@router.put("/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: str, body: TransactionUpdate, user: User = Depends(current_user)
):
    repo = TransactionRepository(_get_db())
    if not repo.update_details(
        transaction_id, user.id,
        **body.model_dump(exclude_unset=True),
        update_fields=set(body.model_fields_set),
    ):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    return repo.get(transaction_id, user.id)


@router.post("/transactions/{transaction_id}/dismiss")
async def dismiss_transaction(transaction_id: str, user: User = Depends(current_user)):
    repo = TransactionRepository(_get_db())
    if not repo.set_status(transaction_id, TxStatus.DISMISSED, user_id=user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    return {"dismissed": True}


@router.post("/transactions/{transaction_id}/invoices")
async def link_invoice_to_transaction(
    transaction_id: str, body: InvoiceLinkRequest, user: User = Depends(current_user)
):
    db = _get_db()
    transactions = TransactionRepository(db)
    invoices = InvoiceRepository(db)
    tx = transactions.get(transaction_id, user.id)
    invoice = invoices.get(body.invoice_id, user_id=user.id)
    if not tx or not invoice:
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch hoặc hóa đơn.", status=404)
    link_repo = InvoiceTransactionLinkRepository(db)
    amount = body.amount if body.amount is not None else min(tx.amount, invoice.total)
    if amount > tx.amount or amount > invoice.total:
        raise AppError("INVALID_LINK_AMOUNT", "Số tiền liên kết vượt quá số tiền giao dịch hoặc hóa đơn.", status=400)
    return link_repo.create(user.id, tx.id, invoice.id, amount, "manual")


@router.delete("/transactions/{transaction_id}/invoices/{invoice_id}")
async def unlink_invoice_from_transaction(
    transaction_id: str, invoice_id: str, user: User = Depends(current_user)
):
    db = _get_db()
    if not TransactionRepository(db).get(transaction_id, user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    links = InvoiceTransactionLinkRepository(db).list_for_transaction(transaction_id, user.id)
    link = next((item for item in links if item.invoice_id == invoice_id), None)
    if not link or not InvoiceRepository(db).get(invoice_id, user_id=user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy liên kết.", status=404)
    InvoiceTransactionLinkRepository(db).delete(link.id, user.id)
    return {"unlinked": True}


@router.get("/transactions/{transaction_id}/attachments")
async def list_attachments(transaction_id: str, user: User = Depends(current_user)):
    repo = TransactionRepository(_get_db())
    if not repo.get(transaction_id, user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    return repo.list_attachments(transaction_id, user.id)


@router.post("/transactions/{transaction_id}/attachments")
async def upload_attachment(
    transaction_id: str, file: UploadFile = File(...), user: User = Depends(current_user)
):
    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    validated = validate_upload(
        content, file.filename or "attachment", file.content_type or "application/octet-stream",
        settings.max_upload_bytes, settings.allowed_mime_types, settings.max_pdf_pages,
    )
    repo = TransactionRepository(_get_db())
    if not repo.get(transaction_id, user.id):
        raise AppError("NOT_FOUND", "Không tìm thấy giao dịch.", status=404)
    pending = repo.get(transaction_id, user.id)
    if getattr(pending.status, "value", pending.status) != "pending_receipt":
        raise AppError("TRANSACTION_NOT_PENDING", "Giao dịch không còn chờ chứng từ.", status=409)
    storage = LocalFileStorage(settings.storage_dir)
    key = generate_storage_key(validated.filename, content)
    storage.put(key, content)
    try:
        item = repo.add_attachment(
            uuid.uuid4().hex[:16], transaction_id, user.id, key,
            validated.mime_type, validated.size,
        )
        return item
    except Exception:
        storage.delete(key)
        raise


@router.get("/attachments/{attachment_id}/content")
async def download_attachment(attachment_id: str, user: User = Depends(current_user)):
    settings = get_settings()
    repo = TransactionRepository(_get_db())
    attachment = repo.get_attachment_with_key(attachment_id, user.id)
    if not attachment:
        raise AppError("NOT_FOUND", "Không tìm thấy tệp đính kèm.", status=404)
    content = LocalFileStorage(settings.storage_dir).get(attachment["storage_key"])
    if content is None:
        raise AppError("NOT_FOUND", "Không tìm thấy tệp đính kèm.", status=404)
    return Response(content, media_type=attachment["mime_type"], headers={"Content-Disposition": "inline"})


@router.get("/preferences/place-sharing")
async def get_place_sharing(user: User = Depends(current_user)):
    with _get_db().get_session() as session:
        preference = session.get(UserPaymentPreference, user.id)
        return {"share_place_visits": preference.share_place_visits if preference else False}


@router.put("/preferences/place-sharing")
async def set_place_sharing(enabled: bool, user: User = Depends(current_user)):
    with _get_db().get_session() as session:
        preference = session.get(UserPaymentPreference, user.id)
        if preference is None:
            preference = UserPaymentPreference(user_id=user.id, share_place_visits=enabled)
        preference.share_place_visits = enabled
        session.add(preference)
        if not enabled:
            visits = session.exec(select(SharedPlaceVisit).where(SharedPlaceVisit.user_id == user.id)).all()
            for visit in visits:
                session.delete(visit)
        session.commit()
    return {"share_place_visits": enabled}


@router.post("/places/share")
async def share_place(body: PlaceShareRequest, user: User = Depends(current_user)):
    if not body.confirmed:
        raise AppError("CONFIRMATION_REQUIRED", "Chỉ có thể chia sẻ địa điểm đã xác nhận.", status=400)
    with _get_db().get_session() as session:
        preference = session.get(UserPaymentPreference, user.id)
        if not preference or not preference.share_place_visits:
            raise AppError("SHARING_DISABLED", "Bật chia sẻ địa điểm trước.", status=403)
        session.add(SharedPlaceVisit(user_id=user.id, place_name=body.name, place_kind=body.kind, lat=body.lat, lon=body.lon))
        session.commit()
    return {"shared": True}


@router.get("/places/shared")
async def list_shared_places(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), radius: int = Query(2000, ge=100, le=10000), user: User = Depends(current_user)):
    db = _get_db()
    users = [u.id for u in UserRepository(db).list_verified() if u.id != user.id]
    places = TransactionRepository(db).list_shared_places(lat, lon, radius, users, exclude_user_id=user.id)
    return {"places": places}


@router.get("/places")
async def nearby_places(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), radius: int = Query(2000, ge=100, le=5000), user: User = Depends(current_user)):
    return await _lookup_osm_places(lat, lon, radius)


async def _lookup_osm_places(lat: float, lon: float, radius: int):
    try:
        response = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"format": "jsonv2", "q": "cafe restaurant shop", "viewbox": f"{lon-0.1},{lat+0.1},{lon+0.1},{lat-0.1}", "bounded": 1, "limit": 20},
            headers={"User-Agent": "InvoiceBillingMVP/1.0 (contact: admin@localhost)"}, timeout=4.0,
        )
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, ValueError):
        return {"places": []}
    places = []
    for item in entries:
        try:
            p_lat, p_lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = int(_calculate_distance(lat, lon, p_lat, p_lon) * 1000)
        if distance <= radius:
            places.append({"name": item.get("name") or item.get("display_name", "Địa điểm")[:80], "kind": item.get("type", "place"), "lat": p_lat, "lon": p_lon, "distance_m": distance, "source": "osm"})
    return {"places": places[:20]}



def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    from math import atan2, cos, radians, sin, sqrt

    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _infer_transport_mode(distance_km: float, time_diff_min: float) -> str:
    """Infer transport mode from distance and time."""
    if distance_km == 0:
        return "walk"
    speed = distance_km / (time_diff_min / 60) if time_diff_min > 0 else 0
    if speed < 7:
        return "walk"
    if speed < 25:
        return "bike"
    if speed < 60:
        return "car"
    return "grab"


# ---- Endpoints ----


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    from_date: str | None = None, to_date: str | None = None, user: User = Depends(current_user)
):
    """Get journey timeline data for map visualization.

    Returns transactions with location, ordered by time, grouped into route segments.
    """
    db = _get_db()
    repo = TransactionRepository(db)

    start = _parse_timeline_date(from_date)
    end = _parse_timeline_date(to_date)
    if start and end and start > end:
        raise AppError("INVALID_DATE_RANGE", "Khoảng ngày không hợp lệ.", status=400)

    transactions = repo.list(
        user_id=user.id, status=None, direction=None, start_date=start, end_date=end, limit=200
    )

    # Filter only transactions with location
    located = [
        tx for tx in transactions if tx.location_lat is not None and tx.location_lon is not None
    ]
    located.sort(key=lambda tx: tx.occurred_at or tx.created_at.isoformat())

    if not located:
        return TimelineResponse(
            points=[], bounds=None, total_distance_km=0, total_spent=0, currency="VND"
        )

    # Build timeline points
    points: list[TimelinePoint] = []
    total_distance = 0.0
    total_spent = 0.0
    currency = "VND"

    min_lat = min(tx.location_lat for tx in located)
    max_lat = max(tx.location_lat for tx in located)
    min_lon = min(tx.location_lon for tx in located)
    max_lon = max(tx.location_lon for tx in located)

    for i, tx in enumerate(located):
        # Determine transport mode from previous point
        transport = "walk"
        if i > 0:
            prev = located[i - 1]
            dist = _calculate_distance(
                prev.location_lat, prev.location_lon, tx.location_lat, tx.location_lon
            )
            try:
                prev_time = datetime.fromisoformat(prev.occurred_at or prev.created_at.isoformat())
                curr_time = datetime.fromisoformat(tx.occurred_at or tx.created_at.isoformat())
                time_diff = (curr_time - prev_time).total_seconds() / 60
            except Exception:
                time_diff = 0
            transport = _infer_transport_mode(dist, time_diff)
            total_distance += dist

        # Get merchant name or use address approximation
        name = tx.merchant or f"Điểm {i + 1}"

        points.append(
            TimelinePoint(
                id=tx.id,
                type="transaction",
                lat=tx.location_lat,
                lon=tx.location_lon,
                name=name,
                merchant=tx.merchant,
                amount=tx.amount,
                currency=tx.currency,
                occurred_at=tx.occurred_at or tx.created_at.isoformat(),
                sequence=i + 1,
                transaction_id=tx.id,
                invoice_id=tx.matched_invoice_id or "",
                transport_mode=transport,
            )
        )
        total_spent += tx.amount
        currency = tx.currency

    return TimelineResponse(
        points=points,
        bounds={"min_lat": min_lat, "max_lat": max_lat, "min_lon": min_lon, "max_lon": max_lon},
        total_distance_km=round(total_distance, 2),
        total_spent=round(total_spent, 2),
        currency=currency,
    )


@router.get("/places/suggestions", response_model=PlaceSuggestionsResponse)
async def get_place_suggestions(
    lat: float, lon: float, radius: int = 2000, user: User = Depends(current_user)
):
    """Get AI-powered place suggestions based on habits and nearby users."""
    db = _get_db()
    tx_repo = TransactionRepository(db)
    settings = get_settings()

    # Get user's own visit history (from transactions with location)
    txs = tx_repo.list(user_id=user.id, limit=500)
    user_visits = {}
    for tx in txs:
        if tx.location_lat and tx.location_lon:
            key = (round(tx.location_lat, 3), round(tx.location_lon, 3))
            name = tx.merchant or "Địa điểm không tên"
            if key not in user_visits:
                user_visits[key] = {
                    "name": name,
                    "count": 0,
                    "lat": tx.location_lat,
                    "lon": tx.location_lon,
                }
            user_visits[key]["count"] += 1

    # Get shared places from other users (anonymized)
    user_repo = UserRepository(db)
    other_user_ids = [u.id for u in user_repo.list_verified() if u.id != user.id]
    with db.get_session() as session:
        opt_ins = {p.user_id for p in session.exec(select(UserPaymentPreference).where(UserPaymentPreference.share_place_visits == True)).all()}
    other_user_ids = [uid for uid in other_user_ids if uid in opt_ins]
    shared = tx_repo.list_shared_places(lat, lon, radius, other_user_ids, exclude_user_id=user.id)

    suggestions: list[PlaceSuggestion] = []

    # 1. User's frequent places nearby
    for key, visit in user_visits.items():
        dist = _calculate_distance(lat, lon, visit["lat"], visit["lon"]) * 1000
        if dist <= radius and visit["count"] >= 2:
            suggestions.append(
                PlaceSuggestion(
                    name=visit["name"],
                    kind="frequent",
                    lat=visit["lat"],
                    lon=visit["lon"],
                    distance_m=int(dist),
                    reason=f"đã ghé {visit['count']} lần",
                    confidence=min(0.9, 0.5 + visit["count"] * 0.1),
                    source="history",
                    visit_count=visit["count"],
                )
            )

    # 2. Shared places from community
    for place in shared[:10]:
        dist = _calculate_distance(lat, lon, place["lat"], place["lon"]) * 1000
        if dist <= radius:
            suggestions.append(
                PlaceSuggestion(
                    name=place["name"],
                    kind=place["kind"],
                    lat=place["lat"],
                    lon=place["lon"],
                    distance_m=int(dist),
                    reason=f"{place['visitors']} người khác đã ghé",
                    confidence=0.7,
                    source="shared",
                    visit_count=place["visitors"],
                )
            )

    # 3. Nearby OpenStreetMap suggestions
    try:
        nearby = await nearby_places(lat=lat, lon=lon, radius=radius, user=user)
        for place in nearby["places"]:
            suggestions.append(PlaceSuggestion(
                name=place["name"], kind=place["kind"], lat=place["lat"], lon=place["lon"],
                distance_m=place["distance_m"], reason="gần bạn", confidence=0.4,
                source="nearby", visit_count=0,
            ))
    except Exception:
        pass

    suggestions.sort(key=lambda s: (-s.confidence, s.distance_m))

    return PlaceSuggestionsResponse(suggestions=suggestions[:10])


@router.get("/email-status", response_model=EmailStatusResponse)
async def get_email_status(user: User = Depends(current_user)):
    db = _get_db()
    with db.get_session() as session:
        account = session.get(UserEmailAccount, user.id)
    if account:
        return EmailStatusResponse(connected=True, config={"host": account.host, "port": account.port, "username": account.username, "folder": account.folder})
    settings = get_settings()
    if settings.imap_enabled:
        return EmailStatusResponse(connected=True, config={"host": settings.imap_host, "port": settings.imap_port, "username": settings.imap_user, "folder": settings.imap_folder})
    return EmailStatusResponse(connected=False, config=None)


@router.post("/email-connect", response_model=EmailConnectResponse)
async def connect_email(body: EmailConnectRequest, user: User = Depends(current_user)):
    host = body.host.strip().lower()
    if host not in _ALLOWED_IMAP_HOSTS or body.port != 993:
        raise AppError("IMAP_PROVIDER_NOT_ALLOWED", "Nhà cung cấp email này chưa được hỗ trợ.", status=400)
    cipher = _fernet()
    import imaplib

    client = None
    try:
        client = imaplib.IMAP4_SSL(host, body.port, timeout=8)
        client.login(body.username, body.password)
        status, _ = client.select(body.folder)
        if status != "OK":
            raise OSError("folder unavailable")
        status, data = client.search(None, "UNSEEN")
        unseen = len((data[0] or b"").split()) if status == "OK" else 0
    except Exception:
        return EmailConnectResponse(ok=False, message="Không thể kết nối. Kiểm tra thông tin đăng nhập và thử lại.")
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass
    encrypted = cipher.encrypt(body.password.encode()).decode()
    db = _get_db()
    with db.get_session() as session:
        account = session.get(UserEmailAccount, user.id) or UserEmailAccount(user_id=user.id)
        account.host = host
        account.port = body.port
        account.username = body.username
        account.encrypted_password = encrypted
        account.folder = body.folder
        account.interval_seconds = body.interval_seconds
        session.add(account)
        session.commit()
    return EmailConnectResponse(ok=True, message="Kết nối IMAP thành công.", test_result={"unseen_count": unseen, "host": host, "folder": body.folder})


@router.post("/camera-upload", response_model=CameraUploadResponse)
async def camera_upload(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    _rl: None = Depends(rate_limit_dependency("rate_limit_upload")),
):
    """Upload photo from camera capture -> Vision OCR -> extract invoice.

    Uses existing extract_invoice pipeline with LLM fallback.
    """
    content = await file.read()

    # Validate upload
    validated = validate_upload(
        content=content,
        filename=file.filename or "camera.jpg",
        declared_mime=file.content_type or "image/jpeg",
        max_bytes=get_settings().max_upload_bytes,
        allowed_mimes=get_settings().allowed_mime_types,
        max_pdf_pages=get_settings().max_pdf_pages,
    )

    # Save file and remove it if extraction or persistence fails.
    storage = LocalFileStorage(get_settings().storage_dir)
    storage_key = generate_storage_key(file.filename or "camera.jpg", content)
    storage.put(storage_key, content)

    try:
        invoice = extract_invoice(
            content=content,
            mime_type=validated.mime_type,
            source_file=file.filename or "camera.jpg",
            user_id=user.id,
        )
        invoice.file_checksum = compute_fingerprint(content)
        invoice.file_size = validated.size
        invoice.page_count = validated.page_count
        invoice.source_file = storage_key
        db = _get_db()
        saved = InvoiceRepository(db).upsert(invoice)
    except Exception:
        storage.delete(storage_key)
        raise AppError("EXTRACTION_FAILED", "Không thể xử lý tệp hóa đơn.", status=422)

    needs_review = any((prov.confidence or 0) < 0.7 for prov in invoice.provenance.values())
    return CameraUploadResponse(invoice_id=saved.id, message="Đã lưu hóa đơn để rà soát.", needs_review=needs_review)


class ImageDraftResponse(BaseModel):
    status: str
    merchant: str = ""
    item: str = ""
    category: str = ""
    address: str = ""
    location_lat: float | None = None
    location_lon: float | None = None
    amount: float | None = None
    packaging_text: str = ""
    message: str


def _image_draft_fields(raw: str) -> dict[str, str]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("Expected object")
    return {
        key: str(result.get(key) or "").strip()[:limit]
        for key, limit in (
            ("merchant", 120), ("item", 200), ("category", 64),
            ("address", 240), ("packaging_text", 1000),
        )
    }


@router.post("/image-draft", response_model=ImageDraftResponse)
async def create_image_draft(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    _rl: None = Depends(rate_limit_dependency("rate_limit_upload")),
):
    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    validated = validate_upload(
        content=content, filename=file.filename or "image.jpg",
        declared_mime=file.content_type or "application/octet-stream",
        max_bytes=settings.max_upload_bytes, allowed_mimes=settings.allowed_mime_types,
        max_pdf_pages=settings.max_pdf_pages,
    )
    if not validated.mime_type.startswith("image/"):
        raise AppError("IMAGE_REQUIRED", "Dùng ảnh sản phẩm hoặc quán cho bản nháp nhận diện.", status=415)

    provider = get_llm_provider()
    complete_image = getattr(provider, "complete_image", None) if provider else None
    if complete_image is None:
        return ImageDraftResponse(
            status="manual_review_required",
            message="Chưa cấu hình nhà cung cấp hỗ trợ nhận diện ảnh; vui lòng nhập thông tin thủ công.",
        )

    encoded = base64.b64encode(content).decode("ascii")
    mime = validated.mime_type
    try:
        raw = complete_image(
            "Nhận diện sản phẩm/đồ vật hoặc món ăn trong ảnh. Chỉ trả JSON hợp lệ với merchant, item, category, address, packaging_text. "
            "Nếu không chắc hoặc không thấy thông tin, dùng chuỗi rỗng. Không suy đoán giá, số tiền, tọa độ hoặc địa chỉ không nhìn thấy.",
            "Gợi ý tên/loại sản phẩm hoặc món cụ thể khi đủ căn cứ. Chỉ điền nhà bán và địa chỉ nếu nhìn thấy rõ. "
            "Không cần đoán tên quán. packaging_text chỉ ghi chữ đọc được trên bao bì. "
            'Trả JSON: {"merchant":"","item":"","category":"","address":"","packaging_text":""}.',
            f"data:{mime};base64,{encoded}",
        )
        fields = _image_draft_fields(raw)
        packaging_text = fields.pop("packaging_text", "")
        if not packaging_text:
            try:
                packaging_text = _extract_text(content, mime)[:1000]
            except Exception:
                packaging_text = ""
        if not any(fields.values()) and not packaging_text:
            return ImageDraftResponse(
                status="manual_review_required",
                message="Không nhận diện được thông tin rõ ràng; vui lòng nhập và xác nhận thủ công.",
            )
        return ImageDraftResponse(
            status="review_required", **fields, packaging_text=packaging_text,
            message="Đây là gợi ý nhận diện. Hãy kiểm tra và xác nhận trước khi lưu giao dịch.",
        )
    except Exception:
        return ImageDraftResponse(
            status="manual_review_required",
            message="Không thể nhận diện ảnh lúc này; vui lòng nhập thông tin thủ công.",
        )
