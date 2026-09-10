"""Backup and restore for SQLite DB + storage directory.

Pure-function style for testability:
- compute_checksum, _write_backup_manifest, _read_backup_manifest are free functions.
- BackupManager methods accept explicit paths (no hidden globals) so tests can use temp dirs.

Uses only stdlib: zipfile, json, hashlib, shutil, datetime, os, pathlib.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


MANIFEST_NAME = "backup_manifest.json"


def compute_checksum(path: str | os.PathLike, algorithm: str = "sha256") -> str:
    """Compute hex digest of a file. Pure function."""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_backup_manifest(
    manifest_path: Path,
    *,
    db_path: str,
    storage_dir: str,
    backup_path: str,
    db_checksum: str,
    zip_checksum: str,
    file_count: int,
) -> Dict:
    manifest = {
        "version": 1,
        "created_at": _utc_now_iso(),
        "db_path": str(db_path),
        "storage_dir": str(storage_dir),
        "backup_path": str(backup_path),
        "db_checksum": db_checksum,
        "zip_checksum": zip_checksum,
        "file_count": file_count,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _read_backup_manifest(manifest_path: Path) -> Dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


class BackupManager:
    """Create/restore/list/prune backups of the SQLite DB + storage dir.

    All methods accept explicit paths so they remain pure and testable.
    """

    def __init__(
        self,
        db_path: str = "invoices.db",
        storage_dir: str = "data/storage",
        backup_dir: str = "data/backups",
    ):
        self.db_path = db_path
        self.storage_dir = storage_dir
        self.backup_dir = backup_dir

    # ---------- public API ----------

    def create_backup(self, backup_dir: Optional[str] = None) -> str:
        """Create a zip backup of the DB file + storage dir.

        Returns the path to the created zip file.
        """
        backup_root = Path(backup_dir) if backup_dir else Path(self.backup_dir)
        backup_root.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{ts}.zip"
        backup_path = backup_root / backup_name

        db = Path(self.db_path)
        storage = Path(self.storage_dir)

        db_checksum = compute_checksum(db) if db.exists() else ""

        file_count = 0
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # DB file at relative root inside zip
            if db.exists():
                arcname = f"db/{db.name}"
                zf.write(db, arcname)
                file_count += 1

            # Storage dir under storage/ prefix
            if storage.exists():
                for root, _dirs, files in os.walk(storage):
                    for name in files:
                        full = Path(root) / name
                        arcname = f"storage/{full.relative_to(storage)}"
                        zf.write(full, arcname)
                        file_count += 1

            # Manifest
            manifest_payload = {
                "version": 1,
                "created_at": _utc_now_iso(),
                "db_path": str(self.db_path),
                "storage_dir": str(self.storage_dir),
                "db_checksum": db_checksum,
                "file_count": file_count,
            }
            zf.writestr(MANIFEST_NAME, json.dumps(manifest_payload, indent=2))

        zip_checksum = compute_checksum(backup_path)

        # Sidecar manifest for fast listing without opening zip
        sidecar = backup_path.with_suffix(".json")
        _write_backup_manifest(
            sidecar,
            db_path=self.db_path,
            storage_dir=self.storage_dir,
            backup_path=str(backup_path),
            db_checksum=db_checksum,
            zip_checksum=zip_checksum,
            file_count=file_count,
        )

        return str(backup_path)

    def restore_backup(
        self,
        backup_path: str,
        target_db_path: Optional[str] = None,
        target_storage_dir: Optional[str] = None,
    ) -> bool:
        """Restore a backup zip into target locations.

        Validates the DB checksum stored in the manifest before restoring.
        Returns True on success.
        """
        backup = Path(backup_path)
        if not backup.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        db_target = Path(target_db_path) if target_db_path else Path(self.db_path)
        storage_target = Path(target_storage_dir) if target_storage_dir else Path(self.storage_dir)

        with zipfile.ZipFile(backup, "r") as zf:
            manifest_data = zf.read(MANIFEST_NAME)
            manifest = json.loads(manifest_data)
            expected_checksum = manifest.get("db_checksum", "")

            # Extract DB to temp then validate checksum before overwriting target
            db_files = [n for n in zf.namelist() if n.startswith("db/") and n != "db/"]
            # There should be exactly one db file entry (db/<name>)
            db_entries = [n for n in zf.namelist() if n.startswith("db/") and not n.endswith("/")]

            tmp_dir = Path(backup.parent) / f"_restore_tmp_{backup.stem}"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            try:
                zf.extractall(tmp_dir)

                extracted_db = tmp_dir / "db"
                if extracted_db.exists():
                    # Find the actual db file inside extracted db/
                    db_file_candidates = list(extracted_db.iterdir())
                    if db_file_candidates:
                        candidate = db_file_candidates[0]
                        actual_checksum = compute_checksum(candidate)
                        if expected_checksum and actual_checksum != expected_checksum:
                            raise ValueError(
                                f"Backup DB checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
                            )
                        db_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(candidate, db_target)

                extracted_storage = tmp_dir / "storage"
                if extracted_storage.exists():
                    if storage_target.exists():
                        shutil.rmtree(storage_target)
                    shutil.copytree(extracted_storage, storage_target)
            finally:
                if tmp_dir.exists():
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        return True

    def list_backups(self, backup_dir: Optional[str] = None) -> List[Dict]:
        """List available backups with metadata, newest first."""
        backup_root = Path(backup_dir) if backup_dir else Path(self.backup_dir)
        if not backup_root.exists():
            return []

        results: List[Dict] = []
        for sidecar in sorted(backup_root.glob("backup_*.json"), reverse=True):
            try:
                manifest = _read_backup_manifest(sidecar)
                zip_path = Path(manifest.get("backup_path", ""))
                size = zip_path.stat().st_size if zip_path.exists() else 0
                results.append(
                    {
                        "backup_path": str(zip_path),
                        "created_at": manifest.get("created_at"),
                        "db_checksum": manifest.get("db_checksum"),
                        "zip_checksum": manifest.get("zip_checksum"),
                        "file_count": manifest.get("file_count", 0),
                        "size": size,
                    }
                )
            except Exception:
                continue
        return results

    def prune_backups(self, backup_dir: Optional[str] = None, keep_last: int = 10) -> int:
        """Remove oldest backups beyond keep_last. Returns removed count."""
        if keep_last < 0:
            raise ValueError("keep_last must be >= 0")
        backups = self.list_backups(backup_dir)
        if len(backups) <= keep_last:
            return 0

        # list_backups returns newest first; drop from the tail (oldest)
        to_remove = backups[keep_last:]
        removed = 0
        for meta in to_remove:
            zip_path = Path(meta["backup_path"])
            sidecar = zip_path.with_suffix(".json")
            try:
                if zip_path.exists():
                    zip_path.unlink()
                if sidecar.exists():
                    sidecar.unlink()
                removed += 1
            except Exception:
                continue
        return removed
