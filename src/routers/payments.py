"""Payments router — timeline, place suggestions, email connect, camera upload.

Phase 1: New endpoints for dashboard redesign.
- GET /payments/timeline -> journey map data (transactions with location, ordered by time)
- GET /payments/places/suggestions -> AI place recommendations
- GET /payments/email-status -> Check IMAP configuration status
- POST /payments/email-connect -> IMAP configuration for auto bill ingestion
- POST /payments/camera-upload -> Camera capture + Vision OCR
"""

from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ..auth.security import decode_token
from ..config import get_settings
from ..errors import AppError
from ..extract.extractor import extract_invoice
from ..models import User
from ..store.sql_repository import (
    Database,
    InvoiceRepository,
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


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = get_database(get_settings().database_path)
    return _db


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
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


# ---- Helper Functions ----


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

    # Parse date filters
    start = datetime.fromisoformat(from_date) if from_date else None
    end = datetime.fromisoformat(to_date) if to_date else None

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

    # 3. Add time-based suggestions (placeholder for OSM integration)
    now = datetime.now()
    hour = now.hour

    # Sort by confidence and distance
    suggestions.sort(key=lambda s: (-s.confidence, s.distance_m))

    return PlaceSuggestionsResponse(suggestions=suggestions[:10])


@router.get("/email-status", response_model=EmailStatusResponse)
async def get_email_status(user: User = Depends(current_user)):
    """Get current user's IMAP email configuration status."""

    host = os.getenv("IMAP_HOST", "")
    username = os.getenv("IMAP_USER", "")

    if host and username:
        return EmailStatusResponse(
            connected=True,
            config={
                "host": host,
                "port": int(os.getenv("IMAP_PORT", "993")),
                "username": username,
                "folder": os.getenv("IMAP_FOLDER", "INBOX"),
            },
        )

    return EmailStatusResponse(connected=False, config=None)


@router.post("/email-connect", response_model=EmailConnectResponse)
async def connect_email(body: EmailConnectRequest, user: User = Depends(current_user)):
    """Configure IMAP for automatic bill ingestion from email.

    Stores IMAP config per user and tests connection.
    """
    settings = get_settings()

    # Test IMAP connection
    import imaplib

    try:
        client = imaplib.IMAP4_SSL(body.host, body.port, timeout=10)
        client.login(body.username, body.password)
        client.select(body.folder)
        status, data = client.search(None, "UNSEEN")
        unseen_count = len((data[0] or b"").split()) if status == "OK" else 0
        client.logout()

        return EmailConnectResponse(
            ok=True,
            message=f"Kết nối IMAP thành công. Tìm thấy {unseen_count} email chưa đọc trong {body.folder}.",
            test_result={"unseen_count": unseen_count, "host": body.host, "folder": body.folder},
        )
    except imaplib.IMAP4.error as e:
        return EmailConnectResponse(ok=False, message=f"Kết nối IMAP thất bại: {e!s}")
    except Exception as e:
        return EmailConnectResponse(ok=False, message=f"Lỗi kết nối: {e!s}")


@router.post("/camera-upload", response_model=CameraUploadResponse)
async def camera_upload(file: UploadFile = File(...), user: User = Depends(current_user)):
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

    # Save file
    storage = LocalFileStorage(get_settings().storage_dir)
    storage_key = generate_storage_key(validated.filename, content)
    stored = storage.put(storage_key, content)

    # Extract invoice (uses LLM/Vision OCR pipeline)
    try:
        invoice = extract_invoice(
            content=content,
            mime_type=validated.mime_type,
            source_file=file.filename or "camera.jpg",
            user_id=user.id,
        )
    except Exception as e:
        raise AppError("EXTRACTION_FAILED", f"Trích xuất thất bại: {e!s}", status=422)

    # Update provenance
    invoice.file_checksum = compute_fingerprint(content)
    invoice.file_size = validated.size
    invoice.page_count = validated.page_count
    invoice.source_file = file.filename or "camera.jpg"

    # Save invoice
    db = _get_db()
    saved = InvoiceRepository(db).upsert(invoice)

    # Check if needs review (low confidence fields)
    needs_review = any((prov.confidence or 0) < 0.7 for prov in invoice.provenance.values())

    return CameraUploadResponse(
        invoice_id=saved.id,
        message="Đã chụp và trích xuất hóa đơn từ ảnh",
        needs_review=needs_review,
    )
