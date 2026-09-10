"""Backup and restore for SQLite + object storage.

Nguyên tắc (PLAN mục 3 — vận hành):
- Tạo .tar.gz archive chứa SQLite DB và storage directory.
- Dùng SQLite backup API cho nhất quán, fallback to file copy.
- Restore giải nén về đúng đường dẫn.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import List, Optional


META_FILENAME = "backup_meta.json"


class SecurityError(ValueError):
    """Raised when a backup archive contains an unsafe filesystem member."""


def _is_safe_path(base_dir: str | os.PathLike[str], path: str | os.PathLike[str]) -> bool:
    """Return whether *path* is contained by *base_dir* after normalization."""
    base = os.path.abspath(os.fspath(base_dir))
    candidate = os.path.abspath(os.fspath(path))
    try:
        return os.path.commonpath((base, candidate)) == base
    except ValueError:
        return False


class BackupManager:
    """Backup and restore manager."""

    def backup(
        self,
        db_path: str,
        storage_dir: str,
        backup_dir: str,
        label: Optional[str] = None,
    ) -> str:
        """Create a backup archive (.tar.gz).

        Args:
            db_path: Path to SQLite database file.
            storage_dir: Path to storage directory.
            backup_dir: Directory to store backup archives.
            label: Optional label for the backup.

        Returns:
            Path to created backup archive.

        Raises:
            FileNotFoundError: If db_path or storage_dir doesn't exist.
        """
        db_p = Path(db_path)
        storage_p = Path(storage_dir)
        backup_p = Path(backup_dir)

        if not db_p.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
        if not storage_p.exists():
            raise FileNotFoundError(f"Storage directory not found: {storage_dir}")

        backup_p.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = f"backup_{timestamp}"
        if label:
            name += f"_{label}"
        archive_path = backup_p / f"{name}.tar.gz"

        # Create a temp staging directory for consistent snapshot
        staging_root = backup_p / f"_staging_{timestamp}"
        staging_root.mkdir(parents=True, exist_ok=True)
        try:
            # 1. Backup SQLite DB
            staging_db = staging_root / "db"
            staging_db.mkdir()
            self._backup_sqlite(db_p, staging_db / db_p.name)

            # 2. Copy storage directory
            staging_storage = staging_root / "storage"
            shutil.copytree(storage_p, staging_storage)

            # 3. Metadata
            meta = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "label": label or "",
                "db_filename": db_p.name,
                "storage_dirname": storage_p.name,
            }
            (staging_root / META_FILENAME).write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # 4. Create tar.gz
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(staging_root, arcname=".")

        finally:
            # Cleanup staging
            shutil.rmtree(staging_root, ignore_errors=True)

        return str(archive_path)

    def restore(
        self,
        backup_path: str,
        db_path: str,
        storage_dir: str,
    ) -> None:
        """Restore from a backup archive.

        Args:
            backup_path: Path to backup .tar.gz archive.
            db_path: Path to restore SQLite database.
            storage_dir: Path to restore storage directory.

        Raises:
            FileNotFoundError: If backup archive doesn't exist.
            ValueError: If backup archive is invalid.
        """
        archive_p = Path(backup_path)
        if not archive_p.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        db_p = Path(db_path)
        storage_p = Path(storage_dir)

        with tempfile.TemporaryDirectory(
            prefix="_restore_", dir=str(archive_p.parent)
        ) as extract_root_str:
            extract_root = Path(extract_root_str)
            with tarfile.open(archive_p, "r:gz") as tar:
                members = tar.getmembers()
                self._validate_members(tar, members, extract_root)
                tar.extractall(path=extract_root, filter="data")

            # Validate backup before changing runtime destinations.
            meta_file = extract_root / META_FILENAME
            if not meta_file.is_file():
                raise ValueError("Invalid backup: missing metadata")

            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid backup metadata") from exc
            if not isinstance(meta, dict):
                raise ValueError("Invalid backup metadata")

            db_filename = meta.get("db_filename", "invoices.db")
            storage_dirname = meta.get("storage_dirname", "storage")
            if not self._is_safe_basename(db_filename) or not self._is_safe_basename(storage_dirname):
                raise SecurityError("Invalid backup path metadata")

            db_src = extract_root / "db" / db_filename
            storage_src = extract_root / "storage"
            if not _is_safe_path(extract_root, db_src) or not _is_safe_path(extract_root, storage_src):
                raise SecurityError("Backup payload escapes staging directory")
            if not db_src.is_file() or not storage_src.is_dir():
                raise ValueError("Invalid backup: missing database or storage")

            # Copy only after extraction and all validation completed.
            db_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db_src, db_p)
            if storage_p.exists():
                shutil.rmtree(storage_p)
            shutil.copytree(storage_src, storage_p)

    @staticmethod
    def _is_safe_basename(value: object) -> bool:
        if not isinstance(value, str) or not value or value in {".", ".."}:
            return False
        path = PurePosixPath(value)
        return len(path.parts) == 1 and path.name == value

    @staticmethod
    def _validate_members(
        tar: tarfile.TarFile,
        members: list[tarfile.TarInfo],
        staging_dir: Path,
    ) -> None:
        for member in members:
            name = member.name
            member_path = staging_dir / name
            pure_path = PurePosixPath(name)
            if not name or pure_path.is_absolute() or ".." in pure_path.parts:
                raise SecurityError(f"Unsafe archive path: {name!r}")
            if not _is_safe_path(staging_dir, member_path):
                raise SecurityError(f"Archive path escapes staging: {name!r}")
            if member.issym() or member.islnk():
                raise SecurityError(f"Links are not allowed in backup archives: {name!r}")
            if not (member.isdir() or member.isfile()) and not member.isreg():
                raise SecurityError(f"Unsupported archive member: {name!r}")
            if member.issym() or member.islnk():
                link_target = staging_dir / name / member.linkname
                if not _is_safe_path(staging_dir, link_target):
                    raise SecurityError(f"Unsafe archive link: {name!r}")

    def list_backups(self, backup_dir: str) -> List[dict]:
        """List all backup archives in a directory.

        Args:
            backup_dir: Path to backup directory.

        Returns:
            List of backup info dicts with keys:
                path, name, size, created_at, label
        """
        backup_p = Path(backup_dir)
        if not backup_p.exists():
            return []

        backups: List[dict] = []
        for entry in sorted(backup_p.iterdir()):
            if entry.is_file() and entry.suffix == ".gz" and entry.name.startswith("backup_"):
                stat = entry.stat()
                backups.append(
                    {
                        "path": str(entry),
                        "name": entry.name,
                        "size": stat.st_size,
                        "created_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                        "label": self._extract_label(entry.name),
                    }
                )
        return backups

    @staticmethod
    def _backup_sqlite(db_path: Path, dest: Path) -> None:
        """Backup SQLite database using backup API."""
        try:
            src_conn = sqlite3.connect(str(db_path))
            dst_conn = sqlite3.connect(str(dest))
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        except sqlite3.Error as e:
            # Fallback: simple file copy
            shutil.copy2(db_path, dest)
            # Log the fallback in case callers want to know
            # (kept quiet here to avoid import overhead)

    @staticmethod
    def _extract_label(filename: str) -> str:
        """Extract label from backup filename."""
        # Format: backup_YYYYMMDD_HHMMSS_label.tar.gz
        stem = filename.replace(".tar.gz", "")
        parts = stem.split("_", 3)
        if len(parts) >= 4:
            return parts[3]
        return ""
