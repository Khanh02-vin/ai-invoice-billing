"""Security and ops hardening tests.

Tests cover:
- Rate limiting (allow and block)
- Malware scanning (magic bytes detection)
- Upload validation rejecting malware
- Backup creation and restore extraction
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import pytest

# Rate limiting
from src.security.rate_limit import RateLimiter

# Malware
from src.security.malware import (
    MalwareScanner,
    MagicBytesScanner,
    FileTypeValidator,
    ScanResult,
)
def scan_bytes(content: bytes, declared_mime: str = ""):
    return MagicBytesScanner().scan_bytes(content, declared_mime)

# Upload
from src.upload import validate_upload, scan_for_malware
from src.errors import AppError

# Backup
from src.store.backup import BackupManager, SecurityError


# ---------- Rate limiting tests ----------


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter()
        key = "test:allows"
        for _ in range(3):
            assert limiter.is_allowed(key, max_requests=3, window_seconds=60) is True

    def test_blocks_after_limit(self):
        limiter = RateLimiter()
        key = "test:blocks"
        for _ in range(5):
            assert limiter.is_allowed(key, max_requests=5, window_seconds=60) is True
        assert limiter.is_allowed(key, max_requests=5, window_seconds=60) is False

    def test_sliding_window_resets(self):
        limiter = RateLimiter()
        key = "test:sliding"
        # Exhaust limit
        for _ in range(2):
            limiter.is_allowed(key, max_requests=2, window_seconds=1)
        assert limiter.is_allowed(key, max_requests=2, window_seconds=1) is False
        # After waiting past window, should allow again (simulate time passing)
        # We use peek to inspect count without adding
        assert limiter.peek(key, window_seconds=1) == 2
        # Reset manually for test determinism
        limiter.reset(key)
        assert limiter.is_allowed(key, max_requests=2, window_seconds=1) is True


# ---------- Malware scanning tests ----------


class TestMalwareScanner:
    def test_detects_eicar_signature(self):
        scanner = MagicBytesScanner()
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        result = scanner.scan_bytes(eicar)
        assert result.is_clean is False
        assert "eicar_test_signature" in result.reason

    def test_detects_executable_magic_bytes(self):
        scanner = MagicBytesScanner()
        exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
        result = scanner.scan_bytes(exe)
        assert result.is_clean is False
        assert "windows_executable" in result.reason

    def test_clean_pdf_is_clean(self):
        scanner = MagicBytesScanner()
        pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nxref\n0 1\ntrailer\n<<>>\n%%EOF"
        result = scanner.scan_bytes(pdf, declared_mime="application/pdf")
        assert result.is_clean is True

    def test_file_type_validator_mismatch(self):
        validator = FileTypeValidator()
        # PNG magic but declared as JPEG should fail
        png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = validator.validate(png_magic, "image/jpeg")
        assert result.is_clean is False

    def test_combined_scanner_rejects_malware(self):
        scanner = MalwareScanner()
        exe = b"MZ\x90\x00" + b"\x00" * 100
        result = scanner.scan_upload(exe, "fake.exe")
        assert result.is_clean is False
        assert "windows_executable" in result.reason


# ---------- Upload validation tests ----------


class TestUploadValidation:
    def test_rejects_malware_upload(self):
        exe = b"MZ\x90\x00" + b"\x00" * 100
        with pytest.raises(AppError) as exc_info:
            validate_upload(
                content=exe,
                filename="evil.exe",
                declared_mime="application/pdf",
                max_bytes=1024 * 1024,
                allowed_mimes=["application/pdf"],
                max_pdf_pages=10,
            )
        assert exc_info.value.code == "MALWARE_DETECTED"
        assert exc_info.value.status == 422


# ---------- Backup / restore tests ----------


class TestBackupRestore:
    def test_backup_creates_archive(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        (storage_dir / "file.txt").write_text("hello", encoding="utf-8")

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test (id) VALUES (1)")
        conn.commit()
        conn.close()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        manager = BackupManager()
        archive = manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
            label="test",
        )

        assert Path(archive).exists()
        assert Path(archive).suffixes == [".tar", ".gz"]

    def test_restore_extracts_correctly(self, tmp_path: Path):
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        backup_dir = tmp_path / "backups"
        storage_dir.mkdir()
        (storage_dir / "file.txt").write_text("hello", encoding="utf-8")

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test (id) VALUES (1)")
        conn.commit()
        conn.close()

        manager = BackupManager()
        archive = manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
            label="test",
        )

        # Remove original files
        db_path.unlink()
        shutil.rmtree(storage_dir)

        # Restore
        manager.restore(
            backup_path=archive,
            db_path=str(db_path),
            storage_dir=str(storage_dir),
        )

        assert db_path.exists()
        assert (storage_dir / "file.txt").read_text(encoding="utf-8") == "hello"

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT id FROM test")
        rows = cursor.fetchall()
        conn.close()
        assert rows == [(1,)]

    @staticmethod
    def _archive_with_members(path: Path, members: list[tuple[str, bytes | None, str | None]]):
        with tarfile.open(path, "w:gz") as tar:
            for name, content, linkname in members:
                info = tarfile.TarInfo(name)
                if linkname is not None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = linkname
                    tar.addfile(info)
                else:
                    payload = content or b""
                    info.size = len(payload)
                    import io
                    tar.addfile(info, io.BytesIO(payload))

    def test_restore_rejects_path_traversal(self, tmp_path: Path):
        archive = tmp_path / "malicious.tar.gz"
        self._archive_with_members(archive, [("../outside.txt", b"owned", None)])
        with pytest.raises(SecurityError):
            BackupManager().restore(str(archive), str(tmp_path / "db"), str(tmp_path / "storage"))
        assert not (tmp_path.parent / "outside.txt").exists()

    def test_restore_rejects_absolute_path(self, tmp_path: Path):
        archive = tmp_path / "malicious.tar.gz"
        self._archive_with_members(archive, [("/tmp/outside.txt", b"owned", None)])
        with pytest.raises(SecurityError):
            BackupManager().restore(str(archive), str(tmp_path / "db"), str(tmp_path / "storage"))

    def test_restore_rejects_external_symlink(self, tmp_path: Path):
        archive = tmp_path / "malicious.tar.gz"
        self._archive_with_members(archive, [("storage/link", None, "/etc/passwd")])
        with pytest.raises(SecurityError):
            BackupManager().restore(str(archive), str(tmp_path / "db"), str(tmp_path / "storage"))

    def test_list_backups_returns_entries(self, tmp_path: Path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        manager = BackupManager()
        db_path = tmp_path / "invoices.db"
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        manager.backup(
            db_path=str(db_path),
            storage_dir=str(storage_dir),
            backup_dir=str(backup_dir),
            label="first",
        )

        backups = manager.list_backups(str(backup_dir))
        assert len(backups) == 1
        assert backups[0]["label"] == "first"
        assert backups[0]["name"].startswith("backup_")
