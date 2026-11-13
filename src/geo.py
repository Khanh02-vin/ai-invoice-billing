"""Tiện ích địa điểm: khoảng cách trên mặt cầu và chuẩn hoá tên quán."""

from __future__ import annotations

import unicodedata
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def normalize_name(name: str) -> str:
    """Chữ thường, bỏ dấu, gộp khoảng trắng — để "Cà Phê Sữa" và "ca phe sua"
    được coi là cùng một tên khi user ghi chi tiêu lần sau."""
    lowered = unicodedata.normalize("NFD", name.casefold())
    stripped = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    stripped = stripped.replace("đ", "d")
    return " ".join(stripped.split())
