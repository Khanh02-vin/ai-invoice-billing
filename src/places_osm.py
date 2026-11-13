"""Gợi ý địa điểm quanh đây từ OpenStreetMap (Nominatim).

OSM **không có giá**. Mọi kết quả ở đây trả ``priced: False`` và không bao giờ
được chấm điểm "phù hợp mức chi tiêu" — chấm giá cho dữ liệu chưa ai ghi lại là
bịa số. Chúng chỉ dùng để gợi ý tên/toạ độ khi user thêm địa điểm.
"""

from __future__ import annotations

from math import cos, radians

import httpx

from .geo import haversine_km

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "FoodDiscoveryMVP/1.0 (contact: admin@localhost)"
TIMEOUT_SECONDS = 4.0


def _viewbox(lat: float, lon: float, radius_m: int) -> str:
    """Khung nhìn bao quanh (lat, lon) theo bán kính — thay cho ±0.1° cố định."""
    dlat = radius_m / 111_320
    dlon = dlat / max(cos(radians(lat)), 0.01)
    return f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"


def nearby_places(lat: float, lon: float, radius_m: int = 2000, limit: int = 20) -> list[dict]:
    """Quán ăn/uống quanh (lat, lon) trong ``radius_m``. Lỗi mạng → danh sách rỗng."""
    try:
        response = httpx.get(
            f"{NOMINATIM_URL}/search",
            params={
                "format": "jsonv2",
                "q": "cafe restaurant shop",
                "viewbox": _viewbox(lat, lon, radius_m),
                "bounded": 1,
                "limit": limit * 2,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    places: list[dict] = []
    for item in entries if isinstance(entries, list) else []:
        try:
            p_lat, p_lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        distance_m = int(haversine_km(lat, lon, p_lat, p_lon) * 1000)
        if distance_m > radius_m:
            continue
        places.append(
            {
                "name": item.get("name") or (item.get("display_name") or "Địa điểm")[:80],
                "kind": item.get("type") or "place",
                "lat": p_lat,
                "lon": p_lon,
                "distance_m": distance_m,
                "source": "osm",
                "priced": False,
            }
        )
    places.sort(key=lambda place: place["distance_m"])
    return places[:limit]


def reverse_geocode(lat: float, lon: float) -> str | None:
    """Toạ độ → địa chỉ. Best-effort: lỗi mạng trả None."""
    try:
        response = httpx.get(
            f"{NOMINATIM_URL}/reverse",
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lon,
                "zoom": 18,
                "addressdetails": 1,
                "accept-language": "vi",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        display = response.json().get("display_name")
    except (httpx.HTTPError, ValueError):
        return None
    return (display or "").strip() or None
