"""Script vận hành backup/restore cho bản beta self-host.

Môi trường đọc từ biến:
  DATABASE_PATH        đường dẫn SQLite (mặc định invoices.db)
  STORAGE_DIR          thư mục chứng từ (mặc định data/storage)
  BACKUP_DIR           thư mục lưu archive (mặc định data/backups)
  RATE_LIMIT...        không dùng ở script này

Ví dụ:
  python scripts/backup.py backup --label pre-upgrade
  python scripts/backup.py list
  python scripts/backup.py restore --archive data/backups/backup_....tar.gz
  python scripts/backup.py prune --keep-last 10

Lưu ý: restore nên chạy khi backend và worker đã dừng để tránh ghi đè database
đang được kết nối. Script không tự dừng dịch vụ.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Cho phép chạy trực tiếp từ thư mục repo mà không cần cài package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.store.backup import BackupManager  # noqa: E402


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _print(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup/restore hóa đơn & thanh toán")
    sub = parser.add_subparsers(dest="action", required=True)

    p_backup = sub.add_parser("backup", help="Tạo archive backup")
    p_backup.add_argument("--label", default=None, help="Nhãn dự phòng")
    p_backup.add_argument("--db-path", default=_env("DATABASE_PATH", "invoices.db"))
    p_backup.add_argument("--storage-dir", default=_env("STORAGE_DIR", "data/storage"))
    p_backup.add_argument("--backup-dir", default=_env("BACKUP_DIR", "data/backups"))

    p_restore = sub.add_parser("restore", help="Khôi phục từ archive")
    p_restore.add_argument("--archive", required=True)
    p_restore.add_argument("--db-path", default=_env("DATABASE_PATH", "invoices.db"))
    p_restore.add_argument("--storage-dir", default=_env("STORAGE_DIR", "data/storage"))

    p_list = sub.add_parser("list", help="Liệt kê archive backup")
    p_list.add_argument("--backup-dir", default=_env("BACKUP_DIR", "data/backups"))

    p_prune = sub.add_parser("prune", help="Giữ lại số lượng archive mới nhất")
    p_prune.add_argument("--backup-dir", default=_env("BACKUP_DIR", "data/backups"))
    p_prune.add_argument("--keep-last", type=int, default=10)

    args = parser.parse_args(argv)
    manager = BackupManager()

    try:
        if args.action == "backup":
            archive = manager.backup(
                db_path=args.db_path,
                storage_dir=args.storage_dir,
                backup_dir=args.backup_dir,
                label=args.label,
            )
            _print({"action": "backup", "archive": archive})
        elif args.action == "restore":
            manager.restore(
                backup_path=args.archive,
                db_path=args.db_path,
                storage_dir=args.storage_dir,
            )
            _print({"action": "restore", "archive": args.archive, "status": "restored"})
        elif args.action == "list":
            _print({"action": "list", "backups": manager.list_backups(args.backup_dir)})
        elif args.action == "prune":
            removed = manager.prune_backups(
                backup_dir=args.backup_dir, keep_last=args.keep_last
            )
            _print({"action": "prune", "removed": removed, "keep_last": args.keep_last})
        else:  # pragma: no cover - argparse handles unknown subcommands
            parser.error(f"unknown action: {args.action}")
    except Exception as exc:  # pragma: no cover - CLI error boundary
        _print({"error": type(exc).__name__, "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
