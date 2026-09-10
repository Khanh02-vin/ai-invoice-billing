"""Object storage interface — lưu file gốc và artifact.

Nguyên tắc (PLAN mục 3):
- Lưu file gốc và artifact qua object-storage interface; không phụ thuộc temp path.
- SQLite vẫn được giữ cho local/demo, nhưng phải có repository abstraction.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class StoredObject:
    """Đối tượng đã lưu."""
    key: str
    path: str
    size: int
    checksum_sha256: str
    created_at: str


class StorageBackend(ABC):
    """Interface cho object storage — có thể thay bằng S3/MinIO sau."""

    @abstractmethod
    def put(self, key: str, content: bytes) -> StoredObject:
        ...

    @abstractmethod
    def get(self, key: str) -> Optional[bytes]:
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...


class LocalFileStorage(StorageBackend):
    """Lưu trữ file trên đĩa — dùng cho local/demo."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _full_path(self, key: str) -> Path:
        # Chống path traversal: chỉ giữ basename
        safe_key = Path(key).name
        return self.base_dir / safe_key

    def put(self, key: str, content: bytes) -> StoredObject:
        path = self._full_path(key)
        path.write_bytes(content)
        return StoredObject(
            key=key,
            path=str(path),
            size=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def get(self, key: str) -> Optional[bytes]:
        path = self._full_path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        path = self._full_path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._full_path(key).exists()


def generate_storage_key(filename: str, content: bytes) -> str:
    """Tạo key duy nhất cho file — dùng checksum + uuid để tránh trùng."""
    checksum = hashlib.sha256(content).hexdigest()[:16]
    ext = Path(filename).suffix
    unique = uuid.uuid4().hex[:8]
    return f"{checksum}-{unique}{ext}"


def compute_fingerprint(content: bytes) -> str:
    """Fingerprint để dedup invoice — dùng SHA-256 của content."""
    return hashlib.sha256(content).hexdigest()
