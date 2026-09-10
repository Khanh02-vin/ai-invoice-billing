"""Middleware: CORS allowlist, security headers, request ID, structured logging.

Nguyên tắc (PLAN mục 3):
- CORS allowlist; security headers; request ID; structured logging.
- Không có CORS wildcard trong production.
- Request ID cho trace và audit.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Gán request_id cho mỗi request — dùng cho logging, audit, error trace."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Security headers chuẩn: no-sniff, frame, xss, hsts, content-type."""

    def __init__(self, app: FastAPI):
        super().__init__(app)
        self._headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for key, value in self._headers.items():
            response.headers[key] = value
        # HSTS chỉ cho HTTPS
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Access log cấu trúc: method, path, status, duration, request_id."""

    def __init__(self, app: FastAPI):
        super().__init__(app)
        self._logger = logging.getLogger("invoice.access")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        request_id = getattr(request.state, "request_id", "-")
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            self._logger.warning(
                "unhandled_exception request_id=%s method=%s path=%s",
                request_id, request.method, str(request.url.path),
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self._logger.info(
                "access request_id=%s method=%s path=%s status=%d duration_ms=%.2f",
                request_id, request.method, str(request.url.path), status, duration_ms,
            )
        return response


def configure_cors(app: FastAPI, origins: list[str]) -> None:
    """Đăng ký CORS theo danh sách origin cho phép — không wildcard."""
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
