"""Offline helpers for calibrating invoice extraction thresholds."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def knee(points):
    """Return the point farthest below the straight line between curve endpoints."""
    points = list(points)
    if len(points) < 3:
        return points[-1] if points else None
    x0, y0 = points[0]
    x1, y1 = points[-1]
    dx, dy = x1 - x0, y1 - y0
    scale = (dx * dx + dy * dy) ** 0.5
    if scale == 0:
        return points[0]
    return max(points[1:-1], key=lambda p: abs(dy * p[0] - dx * p[1] + x1 * y0 - y1 * x0) / scale)


def _date(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d.%m.%y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value.lower()


def field_errors(invoice, record: dict) -> dict[str, bool]:
    """Compare extracted invoice fields with SROIE-style ground truth."""
    entities = record.get("entities", {})
    if not entities.get("total") and not entities.get("company"):
        return {"date": _date(invoice.issue_date) != _date(entities.get("date"))}
    extracted = {
        "total": str(invoice.total or 0).replace(",", "").strip(),
        "vendor": (invoice.vendor or "").strip().casefold(),
        "date": _date(invoice.issue_date),
    }
    expected = {
        "total": str(entities.get("total", "")).replace(",", "").strip(),
        "vendor": str(entities.get("company", "")).strip().casefold(),
        "date": _date(entities.get("date")),
    }
    try:
        total_error = abs(float(extracted["total"]) - float(expected["total"])) > 0.01
    except (ValueError, TypeError):
        total_error = extracted["total"] != expected["total"]
    return {
        "total": total_error,
        "vendor": extracted["vendor"] != expected["vendor"],
        "date": extracted["date"] != expected["date"],
    }
