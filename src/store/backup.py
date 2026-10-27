"""Backup and restore for SQLite + object storage.

Nguyên tắc (PLAN mục 3 — vận hành):
- Tạo .tar.gz archive chứa SQLite DB và storage directory.
- Dùng SQLite online backup API cho nhất quán (an toàn với WAL), không fallback
  âm thầm sang sao chép file thô khi backup thất bại.
- Archive được tạo qua file tạm rồi đổi tên công bố khi hoàn tất và kiểm tra.
- Manifest kèm checksum SHA-256 của DB và từng file storage để restore kiểm tra
  trước khi thay thế dữ liệu đang chạy.
- Restore giải nén về vùng staging, validate toàn bộ đường dẫn/link/checksum,
  chạy PRAGMA integrity_check mới thay thế DB/storage.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

BACKUP_FORMAT_VERSION = 1
APP_NAME = "invoice-billing"
APP_VERSION = "3.0.0"

META_FILENAME = "backup_meta.json"
ARCHIVE_SUFFIX = ".tar.gz"
SIDECAR_SUFFIX = ".sha256"
STAGING_PREFIX = "_staging_"
RESTORE_TMP_SUFFIX = ".restore.tmp"
STORAGE_OLD_SUFFIX = ".old"


class BackupError(Exception):
    """Lỗi vận hành backup/restore (không phải lỗi truy cập archive độc hại)."""


class SecurityError(ValueError):
    """Raised when a backup archive contains an unsafe filesystem member."""


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: str | os.PathLike[str]) -> str:
    """Tính SHA-256 của một file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_label(label: str | None) -> str:
    """Làm sạch nhãn dùng trong tên file backup."""
    if not label:
        return "nolabel"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in label)
    return safe or "nolabel"


def _next_backup_name(backup_dir: Path, label: str | None) -> str:
    """Tên archive tránh trùng khi chạy nhiều lần trong cùng giây."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return f"backup_{stamp}_{_safe_label(label)}_{uuid.uuid4().hex[:8]}{ARCHIVE_SUFFIX}"


def _extract_label(filename: str) -> str:
    """Trích nhãn từ tên file archive.

    Định dạng: backup_<timestamp>_<label_or_nolabel>_<uuid8>.tar.gz
    """
    stem = Path(filename).name
    if stem.endswith(ARCHIVE_SUFFIX):
        stem = stem[: -len(ARCHIVE_SUFFIX)]
    parts = stem.split("_")
    if len(parts) >= 3 and parts[0] == "backup":
        label = parts[2]
        return "" if label == "nolabel" else label
    return ""


def _is_safe_path(base_dir: str | os.PathLike[str], path: str | os.PathLike[str]) -> bool:
    """Trả về True nếu *path* nằm trong *base_dir* sau khi chuẩn hóa."""
    base = os.path.abspath(os.fspath(base_dir))
    candidate = os.path.abspath(os.fspath(path))
    try:
        return os.path.commonpath((base, candidate)) == base
    except ValueError:
        return False


def _is_safe_basename(value: object) -> bool:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    pure = PurePosixPath(value)
    return len(pure.parts) == 1 and pure.name == value


def _validate_members(
    tar: tarfile.TarFile, members: list[tarfile.TarInfo], staging_dir: Path
) -> None:
    """Loại bỏ archive có đường dẫn tuyệt đối, traversal, symlink/hardlink."""
    for member in members:
        name = member.name
        pure = PurePosixPath(name)
        if not name or pure.is_absolute() or ".." in pure.parts:
            raise SecurityError(f"Unsafe archive path: {name!r}")
        member_path = staging_dir / name
        if not _is_safe_path(staging_dir, member_path):
            raise SecurityError(f"Archive path escapes staging: {name!r}")
        if member.issym() or member.islnk():
            raise SecurityError(f"Links are not allowed in backup archives: {name!r}")
        if not (member.isdir() or member.isfile() or member.isreg()):
            raise SecurityError(f"Unsupported archive member: {name!r}")


def _integrity_check_sqlite(db_path: Path) -> None:
    """Chạy PRAGMA integrity_check trên file DB staging; ném nếu không phải 'ok'."""
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"Cannot open SQLite database for integrity check: {exc}") from exc
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite integrity_check failed: {exc}") from exc
    finally:
        conn.close()
    if not rows or any(str(row[0]) != "ok" for row in rows):
        raise BackupError(f"SQLite integrity_check failed for {db_path}")


def _backup_sqlite(db_path: Path, dest: Path) -> None:
    """Sao chép DB qua SQLite online backup API (an toàn với WAL)."""
    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(dest))
        src_conn.backup(dst_conn)
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite online backup failed: {exc}") from exc
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()


def _copy_storage_tree(src: Path, dest: Path) -> dict[str, str]:
    """Sao chép storage thành file thường (bỏ qua symlink), trả về {rel: sha256}."""
    dest.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    for root, _dirs, files in os.walk(src):
        root_path = Path(root)
        for name in files:
            src_file = root_path / name
            if src_file.is_symlink():
                # Storage của ứng dụng chỉ chứa file thường; bỏ qua symlink để
                # tránh vô tình sao chép nội dung ngoài thư mục storage.
                continue
            rel = src_file.relative_to(src)
            arc = rel.as_posix()
            dest_file = dest / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest_file)
            checksums[arc] = _sha256_file(dest_file)
    return checksums


class BackupManager:
    """Quản lý backup/restore cho SQLite DB + thư mục chứng từ."""

    def __init__(self) -> None:
        pass

    def backup(
        self,
        db_path: str = "invoices.db",
        storage_dir: str = "data/storage",
        backup_dir: str = "data/backups",
        label: str | None = None,
    ) -> str:
        """Tạo archive backup (.tar.gz).

        Trả về đường dẫn archive đã công bố. Ném BackupError nếu DB/storage
        không tồn tại hoặc SQLite online backup thất bại.
        """
        db_p = Path(db_path)
        storage_p = Path(storage_dir)
        backup_p = Path(backup_dir)

        if not db_p.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
        if not storage_p.exists():
            raise FileNotFoundError(f"Storage directory not found: {storage_dir}")

        backup_p.mkdir(parents=True, exist_ok=True)
        name = _next_backup_name(backup_dir, label)
        archive_path = backup_p / name
        tmp_archive = backup_p / f".{name}.tmp"

        staging_root = backup_p / f"{STAGING_PREFIX}{uuid.uuid4().hex[:12]}"
        staging_root.mkdir(parents=True, exist_ok=True)
        try:
            staging_db = staging_root / "db"
            staging_db.mkdir()
            _backup_sqlite(db_p, staging_db / db_p.name)

            staged_db_path = staging_db / db_p.name
            staging_storage = staging_root / "storage"
            storage_checksums = _copy_storage_tree(storage_p, staging_storage)

            meta = {
                "version": BACKUP_FORMAT_VERSION,
                "created_at": _iso_now(),
                "label": label or "",
                "app": APP_NAME,
                "app_version": APP_VERSION,
                "db_filename": db_p.name,
                "db_checksum": _sha256_file(staged_db_path),
                "db_size": staged_db_path.stat().st_size,
                "storage_dirname": storage_p.name,
                "storage_file_count": len(storage_checksums),
                "storage_bytes": sum(
                    (staging_storage / rel).stat().st_size
                    for rel in storage_checksums
                ),
                "storage_checksums": storage_checksums,
            }
            (staging_root / META_FILENAME).write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            with tarfile.open(tmp_archive, "w:gz") as tar:
                tar.add(staging_root, arcname=".")

            os.replace(tmp_archive, archive_path)
            archive_checksum = _sha256_file(archive_path)
            sidecar = archive_path.with_name(archive_path.name + SIDECAR_SUFFIX)
            sidecar.write_text(
                f"{archive_checksum}  {archive_path.name}\n", encoding="utf-8"
            )
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

        return str(archive_path)

    def restore(
        self,
        backup_path: str,
        db_path: str = "invoices.db",
        storage_dir: str = "data/storage",
    ) -> None:
        """Khôi phục từ archive backup.

        Không thay đổi dữ liệu đích cho đến khi archive, manifest, checksum và
        integrity_check đều được xác thực. Nếu validation thất bại, dữ liệu
        đích hiện tại được giữ nguyên.
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
                _validate_members(tar, members, extract_root)
                tar.extractall(path=extract_root, filter="data")

            meta_file = extract_root / META_FILENAME
            if not meta_file.is_file():
                raise ValueError("Invalid backup: missing metadata")

            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid backup metadata") from exc
            if not isinstance(meta, dict):
                raise ValueError("Invalid backup metadata")
            if meta.get("version") != BACKUP_FORMAT_VERSION:
                raise ValueError(f"Unsupported backup version: {meta.get('version')!r}")

            db_filename = meta.get("db_filename", "invoices.db")
            storage_dirname = meta.get("storage_dirname", "storage")
            if not _is_safe_basename(db_filename) or not _is_safe_basename(
                storage_dirname
            ):
                raise SecurityError("Invalid backup path metadata")

            db_src = extract_root / "db" / db_filename
            storage_src = extract_root / "storage"
            if not _is_safe_path(extract_root, db_src) or not _is_safe_path(
                extract_root, storage_src
            ):
                raise SecurityError("Backup payload escapes staging directory")
            if not db_src.is_file() or not storage_src.is_dir():
                raise ValueError("Invalid backup: missing database or storage")

            # Xác thực checksum DB trước khi chạm dữ liệu đích.
            expected_db_checksum = meta.get("db_checksum")
            if not expected_db_checksum or _sha256_file(db_src) != expected_db_checksum:
                raise ValueError("Backup database checksum mismatch")

            # Xác thực toàn bộ file storage và không cho file lẻ ngoài manifest.
            expected_storage = meta.get("storage_checksums", {})
            if not isinstance(expected_storage, dict):
                raise ValueError("Invalid backup storage checksums")
            actual_storage: dict[str, str] = {}
            for root, _dirs, files in os.walk(storage_src):
                for name in files:
                    file_path = Path(root) / name
                    rel = file_path.relative_to(storage_src).as_posix()
                    actual_storage[rel] = _sha256_file(file_path)
            if set(actual_storage) != set(expected_storage):
                raise ValueError("Backup storage file set mismatch")
            for rel, expected_checksum in expected_storage.items():
                file_path = storage_src.joinpath(*PurePosixPath(rel).parts)
                if not _is_safe_path(storage_src, file_path):
                    raise SecurityError(f"Unsafe storage path in backup: {rel!r}")
                if _sha256_file(file_path) != expected_checksum:
                    raise ValueError(f"Backup storage checksum mismatch: {rel}")

            _integrity_check_sqlite(db_src)

            # Thay thế DB và storage chỉ sau khi toàn bộ validation hoàn tất.
            db_p.parent.mkdir(parents=True, exist_ok=True)
            db_tmp = db_p.with_name(db_p.name + RESTORE_TMP_SUFFIX)
            try:
                if db_tmp.exists():
                    db_tmp.unlink()
                shutil.copy2(db_src, db_tmp)
                # Loại sidecar WAL cũ khi server đã dừng; SQLite sẽ tạo mới sau khi restore.
                for sidecar in (db_p.with_suffix(db_p.suffix + "-wal"), db_p.with_suffix(db_p.suffix + "-shm")):
                    if sidecar.exists():
                        sidecar.unlink()
                os.replace(db_tmp, db_p)
            finally:
                if db_tmp.exists():
                    db_tmp.unlink(missing_ok=True)

            storage_tmp = storage_p.parent / (storage_p.name + RESTORE_TMP_SUFFIX)
            storage_old = storage_p.parent / (storage_p.name + STORAGE_OLD_SUFFIX)
            try:
                if storage_tmp.exists():
                    shutil.rmtree(storage_tmp, ignore_errors=True)
                shutil.copytree(storage_src, storage_tmp)
                if storage_old.exists():
                    shutil.rmtree(storage_old, ignore_errors=True)
                if storage_p.exists():
                    storage_p.rename(storage_old)
                os.replace(storage_tmp, storage_p)
                shutil.rmtree(storage_old, ignore_errors=True)
            except Exception:
                # Cố gắng hoàn lại storage cũ nếu đã đổi tên.
                if storage_old.exists() and not storage_p.exists():
                    try:
                        storage_old.rename(storage_p)
                    except OSError:
                        pass
                if storage_tmp.exists():
                    shutil.rmtree(storage_tmp, ignore_errors=True)
                raise

    def list_backups(self, backup_dir: str = "data/backups") -> list[dict]:
        """Liệt kê archive backup trong thư mục."""
        backup_p = Path(backup_dir)
        if not backup_p.exists():
            return []

        backups: list[dict] = []
        for entry in sorted(backup_p.iterdir()):
            if entry.is_file() and entry.name.endswith(ARCHIVE_SUFFIX):
                stat = entry.stat()
                backups.append(
                    {
                        "path": str(entry),
                        "name": entry.name,
                        "size": stat.st_size,
                        "created_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=UTC
                        ).isoformat(),
                        "label": _extract_label(entry.name),
                    }
                )
        return backups

    def prune_backups(self, backup_dir: str = "data/backups", keep_last: int = 10) -> int:
        """Xóa archive cũ nhất bên ngoài keep_last. Trả về số lượng đã xóa."""
        if keep_last < 0:
            raise ValueError("keep_last must be >= 0")
        backups = self.list_backups(backup_dir)
        if len(backups) <= keep_last:
            return 0

        removed = 0
        for meta in backups[keep_last:]:
            archive_p = Path(meta["path"])
            try:
                archive_p.unlink()
                removed += 1
            except OSError:
                continue
            sidecar = archive_p.with_name(archive_p.name + SIDECAR_SUFFIX)
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)
        return removed
