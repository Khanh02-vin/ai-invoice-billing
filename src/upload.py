"""Upload validation: giới hạn dung lượng, MIME sniffing, số trang, timeout.

Nguyên tắc (PLAN mục 3):
- Giới hạn upload theo bytes, MIME sniffing bằng content chứ không chỉ extension,
  số trang và thời gian xử lý.
- File quá lớn, sai MIME, file hỏng và file độc hại mẫu bị từ chối.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .errors import AppError
from .security.malware import MalwareScanner, ScanResult


# Magic bytes cho các loại file được phép
_MAGIC_BYTES = {
    "application/pdf": [b"%PDF"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/tiff": [b"II*\x00", b"MM\x00*"],
    "text/plain": [],  # text/plain không có magic bytes — dùng fallback
}

# Kích thước tối đa đọc để sniff magic bytes
_MAX_MAGIC_READ = 16


@dataclass
class ValidatedFile:
    """File đã được validate."""
    filename: str
    content: bytes
    mime_type: str
    size: int
    is_pdf: bool = False
    page_count: Optional[int] = None


def _sniff_mime(content: bytes, declared_mime: str) -> str:
    """Xác định MIME thực sự từ magic bytes — không tin extension."""
    for mime, signatures in _MAGIC_BYTES.items():
        for sig in signatures:
            if content[:len(sig)] == sig:
                return mime
    # text/plain fallback: nếu declared là text và content parseable như text
    if declared_mime == "text/plain":
        try:
            content.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return ""


def _count_pdf_pages(content: bytes) -> Optional[int]:
    """Đếm số trang PDF từ content — đọc pattern /Type /Page."""
    try:
        text = content.decode("latin-1", errors="ignore")
        # Đếm số /Type /Page (không phải /Type /Pages)
        pages = re.findall(r"/Type\s*/Page[^s]", text)
        return len(pages) if pages else None
    except Exception:
        return None


def validate_upload(
    content: bytes,
    filename: str,
    declared_mime: str,
    max_bytes: int,
    allowed_mimes: list[str],
    max_pdf_pages: int,
    malware_scanner: Optional[MalwareScanner] = None,
) -> ValidatedFile:
    """Validate file upload — raise AppError nếu vi phạm.

    Kiểm tra:
    - Dung lượng không vượt max_bytes
    - MIME thực sự (magic bytes) khớp declared và nằm trong danh sách cho phép
    - PDF không vượt max_pdf_pages
    - Malware scanning (signature-based, injectable)
    """
    size = len(content)

    if size == 0:
        raise AppError("EMPTY_FILE", "File rỗng.", status=400)

    if size > max_bytes:
        raise AppError(
            "FILE_TOO_LARGE",
            f"File quá lớn ({size} bytes). Tối đa {max_bytes} bytes.",
            status=413,
            details={"max_bytes": max_bytes, "size": size},
        )

    # Malware scan (before MIME so bad magic bytes are caught even if MIME mismatches)
    scan_for_malware(content, filename, malware_scanner)

    # Sniff MIME từ content
    actual_mime = _sniff_mime(content, declared_mime)

    # Với text/plain, cho phép nếu declared đúng và content decode được
    if actual_mime == "text/plain" and declared_mime == "text/plain":
        pass
    elif not actual_mime:
        raise AppError(
            "INVALID_FILE_TYPE",
            f"Không thể xác định loại file. File có thể bị hỏng hoặc không được hỗ trợ.",
            status=415,
        )
    elif actual_mime != declared_mime:
        raise AppError(
            "MIME_MISMATCH",
            f"Loại file thực sự ({actual_mime}) không khớp khai báo ({declared_mime}).",
            status=415,
        )

    if actual_mime not in allowed_mimes:
        raise AppError(
            "DISALLOWED_FILE_TYPE",
            f"Loại file '{actual_mime}' không được phép.",
            status=415,
            details={"allowed": allowed_mimes},
        )

    is_pdf = actual_mime == "application/pdf"
    page_count = None
    if is_pdf:
        page_count = _count_pdf_pages(content)
        if page_count is not None and page_count > max_pdf_pages:
            raise AppError(
                "PDF_TOO_MANY_PAGES",
                f"PDF có {page_count} trang. Tối đa {max_pdf_pages} trang.",
                status=413,
                details={"max_pages": max_pdf_pages, "pages": page_count},
            )
        if page_count is not None and page_count == 0:
            raise AppError(
                "PDF_NO_PAGES",
                "PDF không có trang hợp lệ.",
                status=400,
            )

    scan_for_malware(content, filename, malware_scanner)

    return ValidatedFile(
        filename=filename,
        content=content,
        mime_type=actual_mime,
        size=size,
        is_pdf=is_pdf,
        page_count=page_count,
    )


def scan_for_malware(
    content: bytes,
    filename: str,
    malware_scanner: Optional[MalwareScanner] = None,
) -> None:
    """Scan file content for malware. Raises AppError if malware detected.

    Args:
        content: Raw file bytes.
        filename: Original filename.
        malware_scanner: Optional MalwareScanner instance. Defaults to MalwareScanner().

    Raises:
        AppError: 422 if malware detected.
    """
    scanner = malware_scanner or MalwareScanner()
    result = scanner.scan_upload(content, filename)
    if not result.is_clean:
        raise AppError(
            "MALWARE_DETECTED",
            f"File rejected: {result.reason}",
            status=422,
            details={"reason": result.reason},
        )
