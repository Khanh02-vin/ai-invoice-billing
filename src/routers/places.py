"""Địa điểm: tìm kiếm, thêm mới, gợi ý từ OpenStreetMap.

Mọi con số tiền ở đây là **mức chi ghi nhận tại địa điểm** (tổng bill chia số
người đã ghi), không phải giá món hay giá niêm yết.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..api.deps import current_user, place_repo, spend_repo
from ..errors import AppError
from ..geo import haversine_km, normalize_name
from ..models import Place, PlaceCategory, User
from ..places_osm import nearby_places as osm_nearby

router = APIRouter(prefix="/places", tags=["places"])

SPEND_LABEL = "mức chi ghi nhận"
VALID_CATEGORIES = {category.value for category in PlaceCategory}


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = PlaceCategory.other.value
    address: str = Field(default="", max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


def _payload(place: Place, stats, lat: float | None = None, lon: float | None = None) -> dict:
    distance_m = None
    if lat is not None and lon is not None and place.lat is not None and place.lon is not None:
        distance_m = int(haversine_km(lat, lon, place.lat, place.lon) * 1000)
    return {
        "id": place.id,
        "name": place.name,
        "category": place.category,
        "address": place.address,
        "lat": place.lat,
        "lon": place.lon,
        "visits": stats.visits if stats else 0,
        "median_spend": stats.median_spend if stats else None,
        "last_visit": stats.last_visit.isoformat() if stats else None,
        "distance_m": distance_m,
        "priced": bool(stats),
        "spend_label": SPEND_LABEL,
    }


@router.get("")
def list_places(
    q: str = "",
    category: str = "",
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    radius_m: int = Query(default=3000, ge=100, le=20_000),
    min_price: int | None = Query(default=None, ge=0),
    max_price: int | None = Query(default=None, ge=0),
    user: User = Depends(current_user),
) -> dict:
    """Tìm địa điểm theo tên/loại/khoảng mức chi, sắp theo khoảng cách nếu có toạ độ.

    ``min_price``/``max_price`` lọc trên **mức chi ghi nhận của chính bạn**; địa
    điểm chưa có dữ liệu bị loại khi có lọc giá (không thể khẳng định nó khớp).
    """
    if category and category not in VALID_CATEGORIES:
        raise AppError("INVALID_CATEGORY", "Loại địa điểm không hợp lệ.", status=400)

    stats = spend_repo().per_place(user.id)
    places = place_repo().search(normalize_name(q) if q else "", category)

    rows = []
    for place in places:
        place_stats = stats.get(place.id)
        if min_price is not None or max_price is not None:
            if place_stats is None:
                continue
            spend = place_stats.median_spend
            if (min_price is not None and spend < min_price) or (
                max_price is not None and spend > max_price
            ):
                continue
        row = _payload(place, place_stats, lat, lon)
        if lat is not None and lon is not None and row["distance_m"] is not None:
            if row["distance_m"] > radius_m:
                continue
        rows.append(row)

    # Gần trước; địa điểm chưa có toạ độ xuống cuối chứ không bị giấu.
    rows.sort(key=lambda row: (row["distance_m"] is None, row["distance_m"] or 0, row["name"]))
    return {"places": rows, "spend_label": SPEND_LABEL}


@router.post("")
def create_place(body: PlaceCreate, user: User = Depends(current_user)) -> dict:
    """Thêm địa điểm. Cùng tên và ≤50 m là cùng một quán — trả quán đã có."""
    if body.category not in VALID_CATEGORIES:
        raise AppError("INVALID_CATEGORY", "Loại địa điểm không hợp lệ.", status=400)

    normalized = normalize_name(body.name)
    if not normalized:
        raise AppError("INVALID_NAME", "Tên địa điểm không hợp lệ.", status=400)

    repo = place_repo()
    place = repo.find_match(normalized, body.lat, body.lon)
    created = place is None
    if place is None:
        place = repo.create(
            name=body.name.strip(),
            normalized_name=normalized,
            category=body.category,
            address=body.address.strip(),
            lat=body.lat,
            lon=body.lon,
            created_by=user.id,
        )

    stats = spend_repo().per_place(user.id).get(place.id)
    return {"place": _payload(place, stats), "created": created}


@router.get("/nearby")
def nearby(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_m: int = Query(default=2000, ge=100, le=5000),
    user: User = Depends(current_user),
) -> dict:
    """Gợi ý địa điểm quanh đây từ OSM. Không có giá → ``priced: false``."""
    return {
        "places": osm_nearby(lat, lon, radius_m),
        "source": "osm",
        "spend_label": SPEND_LABEL,
    }
