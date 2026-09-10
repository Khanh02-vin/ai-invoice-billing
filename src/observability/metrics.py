"""Lightweight Prometheus exposition for ai-invoice-billing.

Attempts to use ``prometheus_client`` if installed; otherwise falls back to
pure stdlib in-memory counters/histograms and manual text exposition.

Metrics:
 - http_requests_total{method,path,status} counter
 - http_request_duration_seconds histogram
 - invoices_created_total counter
 - extraction_duration_seconds histogram
 - uploads_failed_total counter
 - llm_fallback_total counter

Helpers:
 - track_request(method, path, status, duration)
 - inc_invoices_created(amount=1)
 - observe_extraction_duration(seconds)
 - inc_uploads_failed(amount=1)
 - inc_llm_fallback(amount=1)
 - generate_metrics_text() -> str (also alias generate_metrics)
 - metrics_response() -> Response
 - reset_metrics()  # for tests / isolation
"""

from __future__ import annotations

import threading
from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Try prometheus_client first
# ---------------------------------------------------------------------------
_USE_PROM = False
try:
    from prometheus_client import Counter as PromCounter  # type: ignore
    from prometheus_client import Histogram as PromHistogram  # type: ignore
    from prometheus_client import CONTENT_TYPE_LATEST as _PROM_CONTENT_TYPE  # type: ignore
    from prometheus_client import REGISTRY as _PROM_REGISTRY  # type: ignore
    from prometheus_client import generate_latest as _prom_generate_latest  # type: ignore

    _USE_PROM = True
except Exception:  # pragma: no cover - fallback path
    _USE_PROM = False

# ---------------------------------------------------------------------------
# Fallback (stdlib) state
# ---------------------------------------------------------------------------
_lock = threading.Lock()

# Buckets for histograms (seconds)
_HTTP_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
_EXTRACTION_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]

# http_requests_total{method,path,status} -> count
_http_requests_total: Dict[Tuple[str, str, str], int] = {}

# histogram state: bucket_counts, sum, count
_http_bucket_counts: Dict[float, int] = {b: 0 for b in _HTTP_BUCKETS}
_http_bucket_counts[float("inf")] = 0
_http_sum: float = 0.0
_http_count: int = 0

_extraction_bucket_counts: Dict[float, int] = {b: 0 for b in _EXTRACTION_BUCKETS}
_extraction_bucket_counts[float("inf")] = 0
_extraction_sum: float = 0.0
_extraction_count: int = 0

_invoices_created_total: int = 0
_uploads_failed_total: int = 0
_llm_fallback_total: int = 0

# ---------------------------------------------------------------------------
# Prometheus_client objects (if available)
# ---------------------------------------------------------------------------
if _USE_PROM:
    # Use existing collectors if already registered (avoid duplicate registration in tests)
    def _get_or_create_counter(name, doc, labelnames):
        try:
            return _PROM_REGISTRY._names_to_collectors[name]  # type: ignore[attr-defined]
        except KeyError:
            return PromCounter(name, doc, labelnames=labelnames)

    def _get_or_create_histogram(name, doc, labelnames, buckets):
        try:
            return _PROM_REGISTRY._names_to_collectors[name]  # type: ignore[attr-defined]
        except KeyError:
            return PromHistogram(name, doc, labelnames=labelnames, buckets=buckets)

    _prom_http_requests_total = _get_or_create_counter(
        "http_requests_total",
        "Total number of HTTP requests",
        ["method", "path", "status"],
    )
    _prom_http_duration = _get_or_create_histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        [],
        buckets=_HTTP_BUCKETS,
    )
    # extraction histogram without labels for simplicity; can add labels later if needed
    _prom_extraction_duration = _get_or_create_histogram(
        "extraction_duration_seconds",
        "Invoice extraction duration in seconds",
        [],
        buckets=_EXTRACTION_BUCKETS,
    )
    _prom_invoices_created = _get_or_create_counter(
        "invoices_created_total",
        "Total number of invoices created",
        [],
    )
    _prom_uploads_failed = _get_or_create_counter(
        "uploads_failed_total",
        "Total number of failed uploads",
        [],
    )
    _prom_llm_fallback = _get_or_create_counter(
        "llm_fallback_total",
        "Total number of LLM fallback invocations",
        [],
    )
    CONTENT_TYPE = _PROM_CONTENT_TYPE
else:
    CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# Helpers - escaping
# ---------------------------------------------------------------------------
def _escape_label_value(v: str) -> str:
    # Prometheus exposition escaping for label values
    return v.replace("\\", r"\\").replace("\n", r"\n").replace('"', r'\"')


def _normalize_path(path: str) -> str:
    # Keep path as-is but ensure leading slash and strip query
    if not path:
        return "/"
    # remove query string
    if "?" in path:
        path = path.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def track_request(method: str, path: str, status: int | str, duration: float) -> None:
    """Record an HTTP request.

    Args:
        method: HTTP method (GET, POST, ...)
        path: request path e.g. /api/invoices, /health
        status: HTTP status code
        duration: request duration in seconds (float)
    """
    method = str(method).upper()
    path = _normalize_path(str(path))
    status_str = str(status)
    dur = float(duration) if duration is not None else 0.0

    if _USE_PROM:
        _prom_http_requests_total.labels(method=method, path=path, status=status_str).inc()
        _prom_http_duration.observe(dur)
        return

    with _lock:
        key = (method, path, status_str)
        _http_requests_total[key] = _http_requests_total.get(key, 0) + 1

        global _http_sum, _http_count
        _http_sum += dur
        _http_count += 1
        for b in _HTTP_BUCKETS:
            if dur <= b:
                _http_bucket_counts[b] += 1
        _http_bucket_counts[float("inf")] += 1


def inc_invoices_created(amount: int = 1) -> None:
    if _USE_PROM:
        _prom_invoices_created.inc(amount)
        return
    global _invoices_created_total
    with _lock:
        _invoices_created_total += int(amount)


def observe_extraction_duration(seconds: float) -> None:
    dur = float(seconds)
    if _USE_PROM:
        _prom_extraction_duration.observe(dur)
        return
    global _extraction_sum, _extraction_count
    with _lock:
        _extraction_sum += dur
        _extraction_count += 1
        for b in _EXTRACTION_BUCKETS:
            if dur <= b:
                _extraction_bucket_counts[b] += 1
        _extraction_bucket_counts[float("inf")] += 1


def inc_uploads_failed(amount: int = 1) -> None:
    if _USE_PROM:
        _prom_uploads_failed.inc(amount)
        return
    global _uploads_failed_total
    with _lock:
        _uploads_failed_total += int(amount)


def inc_llm_fallback(amount: int = 1) -> None:
    if _USE_PROM:
        _prom_llm_fallback.inc(amount)
        return
    global _llm_fallback_total
    with _lock:
        _llm_fallback_total += int(amount)


def reset_metrics() -> None:
    """Reset all metrics to initial state (useful for tests)."""
    if _USE_PROM:
        # Reset prometheus counters by clearing values is non-trivial.
        # For test isolation we try to clear via internal metrics.
        # Fallback: just reset fallback state and also reset prom where possible.
        try:
            for collector in list(_PROM_REGISTRY._collectors):  # type: ignore[attr-defined]
                pass
        except Exception:
            pass
        # Best-effort: reset our wrapper counters by setting to 0 via _value
        try:
            _prom_http_requests_total._metrics.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            _prom_http_duration._metrics.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        # Histograms also store sum/count internally; clearing metrics clears them
        try:
            _prom_extraction_duration._metrics.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        # For simple counters without labels, set value to 0
        try:
            _prom_invoices_created._value.set(0)  # type: ignore[attr-defined]
            _prom_uploads_failed._value.set(0)  # type: ignore[attr-defined]
            _prom_llm_fallback._value.set(0)  # type: ignore[attr-defined]
        except Exception:
            pass
        # Also clear fallback state for consistency
    with _lock:
        global _http_requests_total, _http_bucket_counts, _http_sum, _http_count
        global _extraction_bucket_counts, _extraction_sum, _extraction_count
        global _invoices_created_total, _uploads_failed_total, _llm_fallback_total
        _http_requests_total.clear()
        _http_bucket_counts = {b: 0 for b in _HTTP_BUCKETS}
        _http_bucket_counts[float("inf")] = 0
        _http_sum = 0.0
        _http_count = 0
        _extraction_bucket_counts = {b: 0 for b in _EXTRACTION_BUCKETS}
        _extraction_bucket_counts[float("inf")] = 0
        _extraction_sum = 0.0
        _extraction_count = 0
        _invoices_created_total = 0
        _uploads_failed_total = 0
        _llm_fallback_total = 0


# Alias for compatibility
reset = reset_metrics


def _generate_fallback_text() -> str:
    lines: list[str] = []

    # http_requests_total
    lines.append("# HELP http_requests_total Total number of HTTP requests")
    lines.append("# TYPE http_requests_total counter")
    with _lock:
        # copy under lock
        items = dict(_http_requests_total)
        h_buckets = dict(_http_bucket_counts)
        h_sum = _http_sum
        h_count = _http_count
        e_buckets = dict(_extraction_bucket_counts)
        e_sum = _extraction_sum
        e_count = _extraction_count
        inv = _invoices_created_total
        upf = _uploads_failed_total
        llm = _llm_fallback_total

    if items:
        for (method, path, status), count in sorted(items.items()):
            lines.append(
                f'http_requests_total{{method="{_escape_label_value(method)}",path="{_escape_label_value(path)}",status="{_escape_label_value(status)}"}} {count}'
            )
    else:
        # expose at least one sample with 0 so metric exists (optional)
        # we skip empty to avoid misleading, but ensure HELP/TYPE present
        pass

    # http_request_duration_seconds histogram
    lines.append("# HELP http_request_duration_seconds HTTP request duration in seconds")
    lines.append("# TYPE http_request_duration_seconds histogram")
    for b in _HTTP_BUCKETS:
        cnt = h_buckets.get(b, 0)
        lines.append(f'http_request_duration_seconds_bucket{{le="{b}"}} {cnt}')
    lines.append(f'http_request_duration_seconds_bucket{{le="+Inf"}} {h_buckets.get(float("inf"), 0)}')
    lines.append(f"http_request_duration_seconds_sum {h_sum}")
    lines.append(f"http_request_duration_seconds_count {h_count}")

    # invoices_created_total
    lines.append("# HELP invoices_created_total Total number of invoices created")
    lines.append("# TYPE invoices_created_total counter")
    lines.append(f"invoices_created_total {inv}")

    # extraction_duration_seconds histogram
    lines.append("# HELP extraction_duration_seconds Invoice extraction duration in seconds")
    lines.append("# TYPE extraction_duration_seconds histogram")
    for b in _EXTRACTION_BUCKETS:
        cnt = e_buckets.get(b, 0)
        lines.append(f'extraction_duration_seconds_bucket{{le="{b}"}} {cnt}')
    lines.append(f'extraction_duration_seconds_bucket{{le="+Inf"}} {e_buckets.get(float("inf"), 0)}')
    lines.append(f"extraction_duration_seconds_sum {e_sum}")
    lines.append(f"extraction_duration_seconds_count {e_count}")

    # uploads_failed_total
    lines.append("# HELP uploads_failed_total Total number of failed uploads")
    lines.append("# TYPE uploads_failed_total counter")
    lines.append(f"uploads_failed_total {upf}")

    # llm_fallback_total
    lines.append("# HELP llm_fallback_total Total number of LLM fallback invocations")
    lines.append("# TYPE llm_fallback_total counter")
    lines.append(f"llm_fallback_total {llm}")

    return "\n".join(lines) + "\n"


def generate_metrics_text() -> str:
    """Return Prometheus exposition text."""
    if _USE_PROM:
        try:
            data = _prom_generate_latest()  # type: ignore
            if isinstance(data, bytes):
                return data.decode("utf-8")
            return str(data)
        except Exception:
            # fallback if generation fails
            return _generate_fallback_text()
    return _generate_fallback_text()


# Alias required by task spec
def generate_metrics() -> str:
    return generate_metrics_text()


def metrics_response():
    """Return a Starlette/FastAPI Response with Prometheus metrics.

    Usage in FastAPI:
        @app.get("/metrics")
        def metrics():
            return metrics_response()
    """
    # Import lazily to avoid circular deps at import time
    try:
        from fastapi.responses import PlainTextResponse  # type: ignore

        return PlainTextResponse(content=generate_metrics_text(), media_type=CONTENT_TYPE)
    except Exception:
        from starlette.responses import Response  # type: ignore

        return Response(content=generate_metrics_text(), media_type=CONTENT_TYPE)


__all__ = [
    "track_request",
    "inc_invoices_created",
    "observe_extraction_duration",
    "inc_uploads_failed",
    "inc_llm_fallback",
    "generate_metrics_text",
    "generate_metrics",
    "metrics_response",
    "reset_metrics",
    "reset",
    "CONTENT_TYPE",
]
