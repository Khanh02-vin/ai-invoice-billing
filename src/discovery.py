"""Chấm điểm địa điểm theo mức chi tiêu ghi nhận được.

Không AI: chỉ thống kê và lọc. Mọi con số ở đây là *mức chi ghi nhận tại địa
điểm* (tổng bill chia số người), không phải giá món hay giá niêm yết.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import PlaceCategory

# Trọng số chưa được tinh chỉnh — giữ đúng như đề bài để sau này đo lại bằng
# dữ liệu thật thay vì sửa cảm tính.
WEIGHTS = {"price": 0.35, "distance": 0.30, "category": 0.20, "history": 0.15}

MIN_SAMPLES = 3

# Band khởi điểm cho 3 loại phổ biến (VND/người). Loại khác không có band mặc
# định — thà nói "chưa đủ dữ liệu" còn hơn bịa một khoảng giá.
DEFAULT_BANDS: dict[str, tuple[int, int]] = {
    PlaceCategory.cafe.value: (30_000, 60_000),
    PlaceCategory.restaurant.value: (50_000, 120_000),
    PlaceCategory.drink.value: (25_000, 50_000),
}

DEFAULT_RADIUS_KM = 3.0
ROUND_TO = 1_000


@dataclass(frozen=True)
class Band:
    """Khoảng mức chi mỗi người của một loại địa điểm."""

    category: str
    low: int
    high: int
    sample_size: int
    is_default: bool

    def contains(self, value: int | None) -> bool:
        return value is not None and self.low <= value <= self.high


def _round_vnd(value: float) -> int:
    return int(round(value / ROUND_TO) * ROUND_TO)


def percentile(values: list[int], fraction: float) -> int:
    """Nội suy tuyến tính, không cần numpy (dữ liệu mẫu rất nhỏ)."""
    ordered = sorted(values)
    if not ordered:
        return 0
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * fraction
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    weight = pos - low
    return _round_vnd(ordered[low] * (1 - weight) + ordered[high] * weight)


def band_from(spend_per_person: list[int], category: str) -> Band | None:
    """Band của ``category`` từ lịch sử chi tiêu; ``None`` khi không đủ dữ liệu
    và loại địa điểm không có band mặc định."""
    samples = [value for value in spend_per_person if value > 0]
    if len(samples) >= MIN_SAMPLES:
        low = percentile(samples, 0.25)
        high = percentile(samples, 0.75)
        return Band(category, low, max(low, high), len(samples), False)
    default = DEFAULT_BANDS.get(category)
    if default is None:
        return None
    return Band(category, default[0], default[1], len(samples), True)


def price_fit(value: int | None, band: Band | None) -> float:
    """1.0 trong band, giảm dần ra ngoài trên độ rộng một band. Không có giá
    ghi nhận → 0.0 (không bao giờ được điểm giá)."""
    if value is None or band is None:
        return 0.0
    if band.low <= value <= band.high:
        return 1.0
    span = max(band.high - band.low, 1)
    gap = band.low - value if value < band.low else value - band.high
    return max(0.0, 1.0 - gap / span)


def distance_score(distance_km: float | None, radius_km: float = DEFAULT_RADIUS_KM) -> float:
    if distance_km is None or radius_km <= 0:
        return 0.0
    return max(0.0, 1.0 - min(distance_km / radius_km, 1.0))


def visit_affinity(visits_at_place: int) -> float:
    """Quán đã ghi 3 lần trở lên = quán quen (H = 1.0)."""
    return min(max(visits_at_place, 0) / 3.0, 1.0)


def score(
    *,
    spend_per_person: int | None,
    band: Band | None,
    distance_km: float | None,
    category_affinity: float,
    history_affinity: float,
    radius_km: float = DEFAULT_RADIUS_KM,
) -> tuple[float, dict[str, float]]:
    """Trả ``(tổng điểm, từng thành phần)`` để UI giải thích được thứ hạng."""
    parts = {
        "price": price_fit(spend_per_person, band),
        "distance": distance_score(distance_km, radius_km),
        "category": min(max(category_affinity, 0.0), 1.0),
        "history": min(max(history_affinity, 0.0), 1.0),
    }
    total = sum(WEIGHTS[key] * value for key, value in parts.items())
    return round(total, 4), {key: round(value, 4) for key, value in parts.items()}
