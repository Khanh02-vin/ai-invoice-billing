"""Tiền tệ an toàn bằng Decimal — tránh lỗi làm tròn float.

Nguyên tắc (PLAN mục 3): dùng Decimal, UTC-aware datetime và schema versioned.
Báo cáo dùng Decimal và currency-aware aggregation.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Dict, List


# Số chữ thập phân theo loại tiền (ISO 4217 minor unit).
# VND/KRW/JPY không có phần thập phân; USD/EUR có 2 chữ số.
ZERO_DECIMAL_CURRENCIES = {"VND", "KRW", "JPY", "CLP", "ISK", "PYG", "RWF"}


def currency_decimal_places(currency: str) -> int:
    """Số chữ số thập phân hợp lệ cho loại tiền."""
    return 0 if currency.upper() in ZERO_DECIMAL_CURRENCIES else 2


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Làm tròn đúng số chữ số thập phân theo loại tiền."""
    places = currency_decimal_places(currency)
    if places == 0:
        return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_decimal(value) -> Decimal:
    """Chuyển giá trị sang Decimal an toàn."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        # Tránh lỗi float bằng cách qua str
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace(" ", "")
        return Decimal(cleaned)
    raise InvalidOperation(f"Không thể chuyển {type(value)} sang Decimal")


def parse_money(value, currency: str = "USD") -> Decimal:
    """Parse giá trị tiền và làm tròn đúng quy tắc loại tiền."""
    return quantize(to_decimal(value), currency)


def add_amounts(amounts: List, currency: str = "USD") -> Decimal:
    """Cộng danh sách tiền an toàn, trả về Decimal đã làm tròn."""
    total = sum(to_decimal(a) for a in amounts)
    return quantize(total, currency)


def format_money(amount: Decimal, currency: str) -> str:
    """Format tiền cho hiển thị (không ký hiệu quốc tế phức tạp)."""
    places = currency_decimal_places(currency)
    if places == 0:
        return f"{int(amount):,} {currency}"
    return f"{amount:,.2f} {currency}"


def aggregate_by_currency(
    items: List[tuple],
) -> Dict[str, Decimal]:
    """Tổng hợp tiền theo loại tiền.

    items: list của (amount, currency) — amount có thể là str/int/float/Decimal.
    Trả về dict currency -> Decimal đã làm tròn.
    """
    buckets: Dict[str, Decimal] = {}
    for amount, currency in items:
        cur = currency.upper()
        buckets[cur] = buckets.get(cur, Decimal("0")) + to_decimal(amount)
    return {cur: quantize(v, cur) for cur, v in buckets.items()}
