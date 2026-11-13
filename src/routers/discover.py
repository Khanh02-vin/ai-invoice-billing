"""Khám phá: hồ sơ mức chi theo loại địa điểm và danh sách quán gợi ý.

Điểm xếp hạng = 0.35·giá + 0.30·khoảng cách + 0.20·loại quen + 0.15·quán quen.
Chưa tinh chỉnh — giữ nguyên trọng số đề bài đưa ra.

Quán **chưa có mức chi ghi nhận** không được chấm điểm giá và không bao giờ nhận
huy hiệu "phù hợp mức chi tiêu": chúng nằm riêng trong ``unpriced``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..api.deps import current_user, place_repo, spend_repo
from ..discovery import MIN_SAMPLES, WEIGHTS, band_from, percentile, score, visit_affinity
from ..errors import AppError
from ..geo import haversine_km, normalize_name
from ..models import PlaceCategory, User

router = APIRouter(prefix="/discover", tags=["discover"])

SPEND_LABEL = "mức chi ghi nhận"
VALID_CATEGORIES = {category.value for category in PlaceCategory}


def _bands(user: User) -> tuple[dict, list[dict]]:
    """Band từng loại + bảng hiển thị (p25/p75/mẫu/ước lượng ban đầu)."""
    categories = {place.id: place.category for place in place_repo().search()}
    samples: dict[str, list[int]] = {}
    for spend in spend_repo().list(user.id):
        category = categories.get(spend.place_id)
        if category:
            samples.setdefault(category, []).append(spend.spend_per_person)

    bands: dict[str, object] = {}
    rows: list[dict] = []
    for category in PlaceCategory:
        values = samples.get(category.value, [])
        band = band_from(values, category.value)
        if band is not None:
            bands[category.value] = band
        rows.append(
            {
                "category": category.value,
                "low": band.low if band else None,
                "high": band.high if band else None,
                "median": percentile(values, 0.5) if values else None,
                "sample_size": len(values),
                "is_default": band.is_default if band else False,
            }
        )
    return bands, rows


@router.get("/bands")
def bands(user: User = Depends(current_user)) -> dict:
    _, rows = _bands(user)
    return {
        "bands": rows,
        "spend_label": SPEND_LABEL,
        "weights": WEIGHTS,
        "min_samples": MIN_SAMPLES,
    }


@router.get("")
def discover(
    lat: float | None = Query(default=None, ge=-90, le=90),
    lon: float | None = Query(default=None, ge=-180, le=180),
    radius_km: float = Query(default=3.0, gt=0, le=50),
    category: str = "",
    q: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
) -> dict:
    if (lat is None) != (lon is None):
        raise AppError("INVALID_COORDINATES", "Cần cả lat và lon.", status=400)
    if category and category not in VALID_CATEGORIES:
        raise AppError("INVALID_CATEGORY", "Loại địa điểm không hợp lệ.", status=400)

    bands_by_category, _ = _bands(user)
    stats = spend_repo().per_place(user.id)
    visits_by_category = spend_repo().category_visits(user.id)
    total_visits = sum(visits_by_category.values())

    results: list[dict] = []
    unpriced: list[dict] = []

    for place in place_repo().search(normalize_name(q) if q else "", category):
        distance_km = None
        if lat is not None and place.lat is not None and place.lon is not None:
            distance_km = round(haversine_km(lat, lon, place.lat, place.lon), 3)
            if distance_km > radius_km:
                continue

        place_stats = stats.get(place.id)
        base = {
            "id": place.id,
            "name": place.name,
            "category": place.category,
            "address": place.address,
            "lat": place.lat,
            "lon": place.lon,
            "distance_km": distance_km,
            "visits": place_stats.visits if place_stats else 0,
            "last_visit": place_stats.last_visit.isoformat() if place_stats else None,
        }

        if place_stats is None:
            unpriced.append({**base, "spend_per_person": None, "spend_label": SPEND_LABEL})
            continue

        band = bands_by_category.get(place.category)
        category_affinity = (
            visits_by_category.get(place.category, 0) / total_visits if total_visits else 0.0
        )
        total_score, parts = score(
            spend_per_person=place_stats.median_spend,
            band=band,
            distance_km=distance_km,
            category_affinity=category_affinity,
            history_affinity=visit_affinity(place_stats.visits),
            radius_km=radius_km,
        )
        results.append(
            {
                **base,
                "score": total_score,
                "parts": parts,
                "spend_per_person": place_stats.median_spend,
                "spend_label": SPEND_LABEL,
                "band": (
                    {
                        "low": band.low,
                        "high": band.high,
                        "is_default": band.is_default,
                    }
                    if band
                    else None
                ),
                "fits": band.contains(place_stats.median_spend) if band else False,
            }
        )

    results.sort(key=lambda row: (-row["score"], row["distance_km"] is None, row["distance_km"] or 0, row["name"]))
    unpriced.sort(key=lambda row: (row["distance_km"] is None, row["distance_km"] or 0, row["name"]))

    return {
        "bounds": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "spend_label": SPEND_LABEL,
        "weights": WEIGHTS,
        "results": results[:limit],
        "unpriced": unpriced[:limit],
        "counts": {"priced": len(results), "unpriced": len(unpriced)},
    }
