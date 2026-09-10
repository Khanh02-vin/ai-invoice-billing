"""Hợp đồng lỗi JSON ổn định — không trả stack trace/PII.

Nguyên tắc (PLAN mục 3): tạo error contract JSON ổn định; không trả stack trace/PII.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("invoice.errors")


class AppError(Exception):
    """Lỗi ứng dụng có mã và status cố định."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def _public_body(code: str, message: str, request_id: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Body lỗi trả cho client — không chứa stack trace hay dữ liệu nhạy cảm."""
    body: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details:
        body["error"]["details"] = details
    return body


def _safe_message(exc: Exception, fallback_status: int) -> str:
    """Chỉ trả message an toàn — ẩn đường dẫn file, connection string, v.v."""
    if isinstance(exc, AppError):
        return exc.message
    if isinstance(exc, StarletteHTTPException):
        return str(exc.detail)
    # Mặc định: không lộ nguyên nhân nội bộ
    return "Có lỗi xảy ra. Vui lòng thử lại."


def register_error_handlers(app: FastAPI) -> None:
    """Đăng ký handler lỗi toàn cục cho FastAPI app."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
        logger.warning("app_error code=%s request_id=%s: %s", exc.code, request_id, exc.message)
        return JSONResponse(
            status_code=exc.status,
            content=_public_body(exc.code, exc.message, request_id, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
        code = _http_status_code(exc.status_code)
        logger.warning("http_error status=%s request_id=%s: %s", exc.status_code, request_id, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_public_body(code, _safe_message(exc, exc.status_code), request_id),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
        logger.exception("unhandled_error request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=_public_body("INTERNAL_ERROR", "Có lỗi xảy ra. Vui lòng thử lại.", request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
        logger.warning("validation_error request_id=%s: %s", request_id, exc.errors())
        # Không trả raw errors (có thể chứa giá trị nhạy cảm)
        return JSONResponse(
            status_code=422,
            content=_public_body(
                "VALIDATION_ERROR",
                "Dữ liệu đầu vào không hợp lệ.",
                request_id,
                # Chỉ trả field path, không trả giá trị
                {"fields": [list(e.get("loc", [])) for e in exc.errors()]},
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
        # Log đầy đủ bên trong, trả message chung bên ngoài
        logger.exception("unexpected_error request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=_public_body("INTERNAL_ERROR", _safe_message(exc, 500), request_id),
        )


def _http_status_code(status: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
    }.get(status, f"HTTP_{status}")
