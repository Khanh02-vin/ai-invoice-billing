"""Quy tắc chấm điểm khám phá — thuần số học, không cần DB."""

from __future__ import annotations

from src.discovery import (
    DEFAULT_BANDS,
    MIN_SAMPLES,
    WEIGHTS,
    band_from,
    distance_score,
    percentile,
    price_fit,
    score,
)
from src.models import PlaceCategory


def test_weights_are_a_proper_average():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_fewer_than_min_samples_falls_back_to_default_band():
    samples = [40_000] * (MIN_SAMPLES - 1)

    band = band_from(samples, PlaceCategory.cafe.value)

    assert band is not None
    assert (band.low, band.high) == DEFAULT_BANDS[PlaceCategory.cafe.value]
    assert band.is_default is True
    assert band.sample_size == MIN_SAMPLES - 1


def test_enough_samples_derives_band_from_history():
    band = band_from([30_000, 40_000, 50_000, 60_000, 70_000], PlaceCategory.cafe.value)

    assert band is not None
    assert band.is_default is False
    assert band.sample_size == 5
    assert band.low == 40_000
    assert band.high == 60_000


def test_unknown_category_without_history_has_no_band():
    """Không đủ dữ liệu và không có band mặc định → không bịa khoảng giá."""
    assert band_from([10_000], PlaceCategory.bakery.value) is None


def test_non_positive_amounts_are_ignored():
    band = band_from([0, -5, 45_000], PlaceCategory.drink.value)

    assert band is not None
    assert band.sample_size == 1
    assert band.is_default is True


def test_price_fit_is_full_inside_and_decays_outside():
    band = band_from([30_000, 40_000, 50_000, 60_000, 70_000], PlaceCategory.cafe.value)

    assert price_fit(50_000, band) == 1.0
    assert price_fit(60_000, band) == 1.0
    assert price_fit(30_000, band) == 0.5  # dưới band 10k trên độ rộng band 20k
    assert price_fit(80_000, band) == 0.0  # cách 20k = đúng độ rộng band
    assert price_fit(None, band) == 0.0


def test_unpriced_place_never_scores_on_price():
    band = band_from([30_000, 40_000, 50_000], PlaceCategory.cafe.value)

    total, parts = score(
        spend_per_person=None,
        band=band,
        distance_km=0.0,
        category_affinity=1.0,
        history_affinity=1.0,
    )

    assert parts["price"] == 0.0
    assert total == round(WEIGHTS["distance"] + WEIGHTS["category"] + WEIGHTS["history"], 4)


def test_band_membership_outranks_distance():
    """Quán trong band nhưng xa hơn phải thắng quán ngoài band ở sát bên."""
    band = band_from([30_000, 40_000, 50_000], PlaceCategory.cafe.value)

    in_band, _ = score(
        spend_per_person=45_000,
        band=band,
        distance_km=2.0,
        category_affinity=0.0,
        history_affinity=0.0,
    )
    too_expensive, _ = score(
        spend_per_person=150_000,
        band=band,
        distance_km=0.0,
        category_affinity=0.0,
        history_affinity=0.0,
    )

    assert in_band > too_expensive


def test_distance_score_reaches_zero_at_radius():
    assert distance_score(0, 3.0) == 1.0
    assert distance_score(1.5, 3.0) == 0.5
    assert distance_score(3.0, 3.0) == 0.0
    assert distance_score(9.0, 3.0) == 0.0
    assert distance_score(None) == 0.0


def test_percentile_interpolates():
    assert percentile([40_000, 50_000, 60_000, 70_000], 0.5) == 55_000
    assert percentile([], 0.5) == 0
    assert percentile([7], 0.9) == 7
