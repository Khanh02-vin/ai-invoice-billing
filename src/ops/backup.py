"""Ops package backup compatibility shim.

DEPRECATED: dùng ``src.store.backup.BackupManager`` thay vì module này.
Module này chỉ giữ lại API cũ của ``src.ops.backup`` và steward mọi gọi sang
implementation chuẩn trong ``src.store.backup`` (SQLite online backup an toàn
với WAL, manifest checksum, validate trước restore).

Không sao chép file DB thô và không dùng ``zipfile.extractall()`` không kiểm tra
như bản opsBackup cũ nữa.
"""

from __future__ import annotations

from src.store.backup import (
    BackupError,
    BackupManager as _StoreBackupManager,
    SecurityError,
)

__all__ = ["BackupManager", "SecurityError", "BackupError"]


class BackupManager(_StoreBackupManager):
    """Wrapper tương thích API cũ, steward hành vi sang store.backup."""

    def __init__(
        self,
        db_path: str = "invoices.db",
        storage_dir: str = "data/storage",
        backup_dir: str = "data/backups",
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.storage_dir = storage_dir
        self.backup_dir = backup_dir

    def create_backup(self, backup_dir: str | None = None) -> str:
        """Tạo backup (giữ tên phương thức cũ)."""
        return super().backup(
            db_path=self.db_path,
            storage_dir=self.storage_dir,
            backup_dir=backup_dir or self.backup_dir,
        )

    def restore_backup(
        self,
        backup_path: str,
        target_db_path: str | None = None,
        target_storage_dir: str | None = None,
    ) -> bool:
        """Khôi phục backup (giữ tên phương thức cũ)."""
        super().restore(
            backup_path=backup_path,
            db_path=target_db_path or self.db_path,
            storage_dir=target_storage_dir or self.storage_dir,
        )
        return True

    def list_backups(self, backup_dir: str | None = None) -> list[dict]:
        """Liệt kê backup (giữ tên phương thức cũ)."""
        return super().list_backups(backup_dir=backup_dir or self.backup_dir)

    def prune_backups(
        self, backup_dir: str | None = None, keep_last: int = 10
    ) -> int:
        """Xóa backup cũ (giữ tên phương thức cũ)."""
        return super().prune_backups(
            backup_dir=backup_dir or self.backup_dir, keep_last=keep_last
        )
