"""Security hardening package (Phase 4).

Nguyên tắc (PLAN mục 3 — bảo mật):
- Rate limiting: chống brute-force, scan, và DDoS nhỏ.
- Malware scanning: phát hiện payload độc hại trước khi lưu trữ.
- Object storage hardening: abstraction cho local/S3/MinIO với fail-safe.

Mọi adapter đều có fallback offline/mock — không phụ thuộc nặng boto3/clamav.
"""
from __future__ import annotations

from .rate_limit import RateLimiter, check_rate_limit
from .malware import ScanResult, MalwareScanner, MagicBytesScanner, FileTypeValidator

__all__ = [
    "RateLimiter",
    "check_rate_limit",
    "ScanResult",
    "MalwareScanner",
    "MagicBytesScanner",
    "FileTypeValidator",
]
