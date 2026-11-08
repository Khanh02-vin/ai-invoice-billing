#!/usr/bin/env python3
"""Idempotent migration: thêm cột location mới (ADR-0002) + sync tables.

Chạy: python scripts/migrate_location_phase1.py [--db PATH]
Mặc định DB_PATH env hoặc invoices.db.
An toàn khi chạy nhiều lần (idempotent).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from store.sql_repository import (
    _ensure_location_columns,
    _ensure_sync_tables,
    _backfill_location_data,
    Database,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "invoices.db"))
    args = parser.parse_args()

    db = Database(args.db)
    _ensure_location_columns(db)
    _ensure_sync_tables(db)
    _backfill_location_data(db)
    print(f"Migration OK: {args.db}")


if __name__ == "__main__":
    main()
