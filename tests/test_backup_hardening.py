"""Kiểm thử hardening cho backup/restore (src/store/backup.py).

Bổ sung các kịch bản chưa có trong tests/test_security_ops.py:
- manifest/version, checksum DB và từng file storage,
- từ chối archive bị chỉnh sửa checksum hay integrity_check,
- giữ nguyên dữ liệu đích khi restore bị từ chối,
- nhiều lần backup liên tiếp không bị trùng tên archive,
- list/prune.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from src.store.backup import BackupError, BackupManager, SecurityError, _sha256_file


def _make_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO test (id) VALUES (1)")
    conn.commit()
    conn.close()


def _make_storage(storage_dir: Path) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "file.txt").write_text("hello", encoding="utf-8")


def _build_archive(
    tmp_path: Path,
    db_path: Path | None = None,
    storage_dir: Path | None = None,
    meta_overrides: dict | None = None,
) -> Path:
    """Xây archive tar.gz thủ công để kiểm thử các trường hợp lỗi manifest."""
    staging = tmp_path / "staging"
    (staging / "db").mkdir(parents=True)
    (staging / "storage").mkdir(parents=True)

    db_checksum = meta_overrides.get("db_checksum") if meta_overrides else None
    if db_checksum is None and db_path and db_path.exists():
        db_checksum = _sha256_file(db_path)

    if db_path and db_path.exists():
        (staging / "db" / db_path.name).write_bytes(db_path.read_bytes())

    storage_checksums = {}
    if storage_dir and storage_dir.exists():
        for root, _dirs, files in os_walk(storage_dir):
            root_path = Path(root)
            for name in files:
                src_file = root_path / name
                rel = src_file.relative_to(storage_dir)
                dest = staging / "storage" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src_file.read_bytes())
                storage_checksums[rel.as_posix()] = _sha256_file(dest)

    meta = {
        "version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "label": "",
        "app": "invoice-billing",
        "app_version": "3.0.0",
        "db_filename": db_path.name if db_path else "invoices.db",
        "db_checksum": db_checksum or "",
        "db_size": 0,
        "storage_dirname": "storage",
        "storage_file_count": len(storage_checksums),
        "storage_bytes": 0,
        "storage_checksums": storage_checksums,
    }
    meta.update(meta_overrides or {})
    (staging / "backup_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    archive = tmp_path / "backup_bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return archive


def os_walk(path: Path):
    """Thin wrapper so tests do not import os unnecessarily."""
    import os

    return os.walk(path)


class TestBackupArchiveFormat:
    def test_backup_contains_metadata_and_sidecar(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        _make_db(db_path)
        _make_storage(storage_dir)

        backup_dir = tmp_path / "backups"
        manager = BackupManager()
        archive = manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
            label="pre-upgrade",
        )

        archive_p = Path(archive)
        assert archive_p.exists()
        sidecar = archive_p.with_name(archive_p.name + ".sha256")
        assert sidecar.exists()

        with tarfile.open(archive_p, "r:gz") as tar:
            names = set(tar.getnames())
            assert "backup_meta.json" in names or "./backup_meta.json" in names
            assert f"./db/{db_path.name}" in names or f"db/{db_path.name}" in names
            assert f"./storage/file.txt" in names or f"storage/file.txt" in names
            meta_name = next(
                n for n in tar.getnames() if PurePosixPath(n).name == "backup_meta.json"
            )
            meta = json.loads(tar.extractfile(meta_name).read())
        assert meta["version"] == 1
        assert meta["label"] == "pre-upgrade"
        assert meta["db_checksum"] != "deadbeef"
        assert meta["storage_checksums"]["file.txt"] != ""

    def test_repeated_backups_do_not_collide(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        _make_db(db_path)
        _make_storage(storage_dir)

        backup_dir = tmp_path / "backups"
        manager = BackupManager()
        first = manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
        )
        second = manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
        )
        assert first != second
        assert Path(first).exists()
        assert Path(second).exists()


class TestRestoreValidation:
    def test_restore_rejects_bad_database_checksum(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        _make_db(db_path)
        _make_storage(storage_dir)

        archive = _build_archive(
            tmp_path,
            db_path=db_path,
            storage_dir=storage_dir,
            meta_overrides={"db_checksum": "wrong_checksum"},
        )

        target_db = tmp_path / "target.db"
        target_storage = tmp_path / "target_storage"
        manager = BackupManager()
        with pytest.raises(ValueError):
            manager.restore(
                backup_path=str(archive),
                db_path=str(target_db),
                storage_dir=str(target_storage),
            )
        assert not target_db.exists()
        assert not target_storage.exists()

    def test_restore_rejects_storage_checksum_mismatch(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        _make_db(db_path)
        _make_storage(storage_dir)

        archive = _build_archive(
            tmp_path,
            db_path=db_path,
            storage_dir=storage_dir,
            meta_overrides={
                "storage_file_count": 1,
                "storage_bytes": 5,
                "storage_checksums": {"file.txt": "wrong_checksum"},
            },
        )

        target_db = tmp_path / "target.db"
        target_storage = tmp_path / "target_storage"
        manager = BackupManager()
        with pytest.raises(ValueError):
            manager.restore(
                backup_path=str(archive),
                db_path=str(target_db),
                storage_dir=str(target_storage),
            )
        assert not target_db.exists()
        assert not target_storage.exists()

    def test_restore_rejects_non_sqlite_database(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        db_path.write_bytes(b"this is not a sqlite database")
        storage_dir = tmp_path / "storage"
        _make_storage(storage_dir)

        archive = _build_archive(
            tmp_path,
            db_path=db_path,
            storage_dir=storage_dir,
            meta_overrides={"db_checksum": _sha256_file(db_path)},
        )

        target_db = tmp_path / "target.db"
        target_storage = tmp_path / "target_storage"
        manager = BackupManager()
        with pytest.raises(BackupError):
            manager.restore(
                backup_path=str(archive),
                db_path=str(target_db),
                storage_dir=str(target_storage),
            )
        assert not target_db.exists()
        assert not target_storage.exists()

    def test_restore_round_trip(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        _make_db(db_path)
        _make_storage(storage_dir)

        backup_dir = tmp_path / "backups"
        manager = BackupManager()
        archive = manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
        )

        target_db = tmp_path / "target.db"
        target_storage = tmp_path / "target_storage"
        manager.restore(
            backup_path=archive,
            db_path=str(target_db),
            storage_dir=str(target_storage),
        )
        assert target_db.exists()
        assert (target_storage / "file.txt").read_text(encoding="utf-8") == "hello"

        conn = sqlite3.connect(str(target_db))
        row = conn.execute("SELECT id FROM test").fetchone()
        conn.close()
        assert row == (1,)

    def test_backup_rejects_missing_database(self, tmp_path: Path):
        manager = BackupManager()
        with pytest.raises(FileNotFoundError):
            manager.backup(
                db_path=str(tmp_path / "missing.db"),
                storage_dir=str(tmp_path / "storage"),
                backup_dir=str(tmp_path / "backups"),
            )

    def test_prune_keeps_last_n(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        _make_db(db_path)
        _make_storage(storage_dir)

        backup_dir = tmp_path / "backups"
        manager = BackupManager()
        for i in range(5):
            manager.backup(
                db_path=str(db_path),
                storage_dir=str(storage_dir),
                backup_dir=str(backup_dir),
                label=f"n{i}",
            )

        removed = manager.prune_backups(backup_dir=str(backup_dir), keep_last=2)
        assert removed == 3
        remaining = manager.list_backups(backup_dir=str(backup_dir))
        assert len(remaining) == 2
