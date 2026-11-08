from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Interpret legacy naive timestamps as UTC and normalize aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
