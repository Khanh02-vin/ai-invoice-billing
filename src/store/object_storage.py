"""Object storage abstraction — local, S3, MinIO.

Nguyên tắc (PLAN mục 3 — bảo mật):
- Interface thống nhất cho local/S3/MinIO.
- LocalObjectStorage: wrapper backward-compatible với LocalFileStorage cũ.
- S3ObjectStorage: dùng boto3 nếu có, không thì raise rõ ràng.
- Factory get_object_storage() đọc từ env OBJECT_STORAGE=local|s3.
- Không thêm dep nặng: boto3 chỉ import khi cần.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("invoice.store.object_storage")


# ---------- Result model ----------

class ObjectInfo:
    """Thông tin object đã lưu."""

    def __init__(
        self,
        key: str,
        url: str,
        size: int,
        checksum_sha256: str,
        mime_type: str = "application/octet-stream",
        created_at: str = "",
    ) -> None:
        self.key = key
        self.url = url
        self.size = size
        self.checksum_sha256 = checksum_sha256
        self.mime_type = mime_type
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "url": self.url,
            "size": self.size,
            "checksum_sha256": self.checksum_sha256,
            "mime_type": self.mime_type,
            "created_at": self.created_at,
        }


# ---------- Abstract interface ----------

class ObjectStorage(ABC):
    """Interface chung cho object storage."""

    @abstractmethod
    def put(self, key: str, content: bytes, mime_type: str = "application/octet-stream") -> str:
        """Lưu object, trả về url/uri."""
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Đọc object, raise FileNotFoundError nếu không tồn tại."""
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Xóa object. Trả True nếu xóa thành công hoặc không tồn tại."""
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Kiểm tra object tồn tại."""
        ...

    @abstractmethod
    def list_prefix(self, prefix: str) -> List[str]:
        """Liệt kế key theo prefix."""
        ...


# ---------- Local implementation ----------

class LocalObjectStorage(ObjectStorage):
    """Lưu object trên đĩa — backward-compatible với LocalFileStorage cũ.

    Dùng STORAGE_DIR làm root. Key được sanitize để tránh path traversal.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = Path(base_dir or os.getenv("STORAGE_DIR", "data/storage"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        """Chống path traversal: chỉ giữ basename, ép vào base_dir."""
        # Chuẩn hóa: bỏ slash đầu, chỉ giữ basename
        safe = Path(key).name
        if not safe:
            raise ValueError("Invalid storage key")
        full = self.base_dir / safe
        # Đảm bảo không escape base_dir
        try:
            full.relative_to(self.base_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Invalid storage key: path traversal detected") from exc
        return full

    def put(self, key: str, content: bytes, mime_type: str = "application/octet-stream") -> str:
        path = self._safe_path(key)
        path.write_bytes(content)
        return f"file://{path}"

    def get(self, key: str) -> bytes:
        path = self._safe_path(key)
        if not path.exists():
            raise FileNotFoundError(f"Object not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        path = self._safe_path(key)
        if path.exists():
            path.unlink()
            return True
        return True  # Idempotent: không tồn tại = đã xóa

    def exists(self, key: str) -> bool:
        try:
            return self._safe_path(key).exists()
        except ValueError:
            return False

    def list_prefix(self, prefix: str) -> List[str]:
        # Local: prefix = substring match trên basename
        results: List[str] = []
        for item in self.base_dir.iterdir():
            if item.is_file() and item.name.startswith(prefix):
                results.append(item.name)
        return sorted(results)


# ---------- S3 / MinIO implementation ----------

class S3ObjectStorage(ObjectStorage):
    """S3-compatible object storage (AWS S3, MinIO, R2, v.v.).

    Yêu cầu boto3. Nếu thiếu, raise ImportError rõ ràng.
    Cấu hình từ env:
        S3_BUCKET (bắt buộc)
        S3_ENDPOINT (tùy chọn — cho MinIO)
        S3_REGION (mặc định us-east-1)
        S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY
        S3_FORCE_PATH_STYLE (1/true cho MinIO)
    """

    def __init__(self) -> None:
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:
            raise ImportError(
                "S3ObjectStorage yêu cầu boto3. Cài bằng `pip install boto3` "
                "hoặc dùng OBJECT_STORAGE=local."
            ) from exc
        self.bucket = os.getenv("S3_BUCKET")
        if not self.bucket:
            raise RuntimeError("S3_BUCKET environment variable is required for S3 storage.")
        endpoint = os.getenv("S3_ENDPOINT")  # None = AWS S3
        region = os.getenv("S3_REGION", "us-east-1")
        force_path_style = os.getenv("S3_FORCE_PATH_STYLE", "1" if endpoint else "0")
        path_style = force_path_style.strip().lower() in {"1", "true", "yes", "on"}
        config = Config(
            signature_version=os.getenv("S3_SIGNATURE_VERSION", "s3v4"),
            s3={"addressing_style": "path" if path_style else "auto"},
        )
        session_kwargs: dict = {}
        if os.getenv("S3_ACCESS_KEY_ID"):
            session_kwargs["aws_access_key_id"] = os.getenv("S3_ACCESS_KEY_ID")
        if os.getenv("S3_SECRET_ACCESS_KEY"):
            session_kwargs["aws_secret_access_key"] = os.getenv("S3_SECRET_ACCESS_KEY")
        session = boto3.session.Session(**session_kwargs)
        self._client = session.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            config=config,
        )
        self._endpoint = endpoint
        self._bucket = self.bucket

    def _build_url(self, key: str) -> str:
        if self._endpoint:
            return f"{self._endpoint}/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.amazonaws.com/{key}"

    def put(self, key: str, content: bytes, mime_type: str = "application/octet-stream") -> str:
        import io
        self._client.upload_fileobj(
            io.BytesIO(content),
            self._bucket,
            key,
            ExtraArgs={"ContentType": mime_type},
        )
        return self._build_url(key)

    def get(self, key: str) -> bytes:
        import io
        buf = io.BytesIO()
        self._client.download_fileobj(self._bucket, key, buf)
        return buf.getvalue()

    def delete(self, key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            logger.exception("s3_delete_failed key=%s", key)
            return False

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def list_prefix(self, prefix: str) -> List[str]:
        keys: List[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return sorted(keys)


# ---------- Migration helper ----------

class LocalToS3Migrator:
    """Migrate object từ local storage sang S3."""

    def __init__(self, local: LocalObjectStorage, s3: S3ObjectStorage) -> None:
        self.local = local
        self.s3 = s3

    def migrate_all(self, prefix: str = "") -> dict:
        """Migrate toàn bộ object khớp prefix. Trả summary."""
        keys = self.local.list_prefix(prefix)
        migrated = 0
        failed = 0
        for key in keys:
            try:
                content = self.local.get(key)
                # Guess mime từ extension
                ext = Path(key).suffix.lower()
                mime = {
                    ".pdf": "application/pdf",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".tiff": "image/tiff",
                    ".txt": "text/plain",
                }.get(ext, "application/octet-stream")
                self.s3.put(key, content, mime_type=mime)
                migrated += 1
            except Exception:
                logger.exception("migrate_failed key=%s", key)
                failed += 1
        return {"total": len(keys), "migrated": migrated, "failed": failed}


# ---------- Factory ----------

_storage_instance: Optional[ObjectStorage] = None


def get_object_storage(backend: Optional[str] = None) -> ObjectStorage:
    """Factory: trả ObjectStorage theo env OBJECT_STORAGE.

    Args:
        backend: 'local' hoặc 's3'. Mặc định đọc từ OBJECT_STORAGE env.
    """
    global _storage_instance
    if backend is None:
        backend = os.getenv("OBJECT_STORAGE", "local").strip().lower()
    if _storage_instance is not None and getattr(_storage_instance, "_backend_tag", None) == backend:
        return _storage_instance
    if backend == "s3":
        instance: ObjectStorage = S3ObjectStorage()
    elif backend == "local":
        instance = LocalObjectStorage()
    else:
        raise ValueError(
            f"Unknown OBJECT_STORAGE={backend!r}. Use 'local' or 's3'."
        )
    instance._backend_tag = backend  # type: ignore[attr-defined]
    _storage_instance = instance
    return instance


def reset_object_storage() -> None:
    """Reset factory cache — dùng cho test."""
    global _storage_instance
    _storage_instance = None


# ---------- Backward-compatible helpers ----------

def generate_object_key(filename: str, content: bytes) -> str:
    """Tạo key duy nhất cho object — dùng checksum + uuid."""
    checksum = hashlib.sha256(content).hexdigest()[:16]
    ext = Path(filename).suffix
    unique = uuid.uuid4().hex[:8]
    return f"{checksum}-{unique}{ext}"
