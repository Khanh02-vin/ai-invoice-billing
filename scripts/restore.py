#!/usr/bin/env python3
"""CLI restore script for ai-invoice-billing.

Usage:
    python scripts/restore.py data/backups/backup_20260909_120000.tar.gz --db invoices.db --storage data/storage
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from store.backup import BackupManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore invoices DB and storage from backup.")
    parser.add_argument("backup_path", help="Path to backup .tar.gz archive")
    parser.add_argument("--db", required=True, help="Path to restore SQLite database")
    parser.add_argument("--storage", required=True, help="Path to restore storage directory")
    args = parser.parse_args()

    manager = BackupManager()
    manager.restore(
        backup_path=args.backup_path,
        db_path=args.db,
        storage_dir=args.storage,
    )
    print(f"Restored from: {args.backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
