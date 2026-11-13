"""Regression: DB cũ dùng bảng token schema legacy (token_hash) phải được
nâng cấp để repository SQLModel (id/jti) ghi được — nếu không register/login
trả 500 "table refresh_tokens has no column named id"."""

from __future__ import annotations

import sqlite3

from sqlmodel import SQLModel

from src.store import sql_repository
from src.store.sql_repository import UserRepository, init_database

LEGACY_DDL = (
    """CREATE TABLE refresh_tokens (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
        revoked INTEGER DEFAULT 0, created_at TEXT NOT NULL, family TEXT NOT NULL
    )""",
    """CREATE TABLE email_verifications (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
        used INTEGER DEFAULT 0, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE password_resets (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
        used INTEGER DEFAULT 0, created_at TEXT NOT NULL
    )""",
)


def test_init_database_migrates_legacy_auth_token_tables(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    with sqlite3.connect(db_path) as conn:
        for ddl in LEGACY_DDL:
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO refresh_tokens VALUES ('old', 'u', '2099-01-01', 0, 'now', 'fam')"
        )

    original = sql_repository._database
    try:
        repo = UserRepository(init_database(db_path))
        user = repo.create("legacy", "hash", "legacy@example.com")

        repo.store_refresh_token("jti-1", user.id, "2099-01-01T00:00:00+00:00")
        assert repo.is_refresh_token_valid("jti-1") == user.id

        repo.create_email_verification(user.id, "verify-1", "2099-01-01T00:00:00+00:00")
        assert repo.consume_email_verification("verify-1") == user.id

        repo.create_password_reset(user.id, "reset-1", "2099-01-01T00:00:00+00:00")
        assert repo.consume_password_reset("reset-1") == user.id
    finally:
        sql_repository._database = original


LEGACY_PARTIAL_DDL = (
    "CREATE TABLE organizations (id TEXT PRIMARY KEY)",
    "CREATE TABLE invoices (id TEXT PRIMARY KEY)",
    "CREATE TABLE payment_transactions (id TEXT PRIMARY KEY)",
)


def test_init_database_adds_columns_missing_from_legacy_tables(tmp_path):
    """Bảng do version cũ tạo thiếu cột model mới (location_*, transport_mode,
    slug...) — create_all không ALTER bảng có sẵn nên SELECT sẽ 500."""
    db_path = str(tmp_path / "partial.db")
    with sqlite3.connect(db_path) as conn:
        for ddl in LEGACY_PARTIAL_DDL:
            conn.execute(ddl)

    original = sql_repository._database
    try:
        db = init_database(db_path)
        missing: dict[str, list[str]] = {}
        with db.engine.connect() as conn:
            for table in SQLModel.metadata.sorted_tables:
                have = {
                    row[1]
                    for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")
                }
                if not have:
                    continue
                cols = [c.name for c in table.columns if c.name not in have]
                if cols:
                    missing[table.name] = cols
        assert missing == {}
    finally:
        sql_repository._database = original
