#!/usr/bin/env python3
"""CLI backup script for ai-invoice-billing.

Usage:
    python scripts/backup.py --db invoices.db --storage data/storage --backup-dir data/backups
    python scripts/backup.py --db invoices.db --storage data/storage --backup-dir data/backups --label weekly
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from store.backup import BackupManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup invoices DB and storage.")
    parser.add_argument("--db", required=True, help="Path to SQLite database file")
    parser.add_argument("--storage", required=True, help="Path to storage directory")
    parser.add_argument("--backup-dir", required=True, help="Directory to store backup archives")
    parser.add_argument("--label", default=None, help="Optional label for the backup")
    args = parser.parse_args()

    manager = BackupManager()
    archive_path = manager.backup(
        db_path=args.db,
        storage_dir=args.storage,
        backup_dir=args.backup_dir,
        label=args.label,
    )
    print(f"Backup created: {archive_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
