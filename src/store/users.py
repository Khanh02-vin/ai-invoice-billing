"""Repository người dùng trên SQLite."""
import hashlib
import sqlite3
from datetime import datetime
from typing import Optional

from ..domain.models import User
from .db import SQLiteRepo


class UserRepository(SQLiteRepo):
    """Lưu trữ người dùng (bảng users)."""

    def _ensure_schema(self, conn: sqlite3.Connection):
        """Tạo bảng users nếu chưa tồn tại."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT
            )
        """)

    def create(self, username: str, password_hash: str) -> User:
        """Tạo người dùng mới. Raise ValueError nếu username đã tồn tại."""
        user = User(
            id=hashlib.md5(username.encode()).hexdigest()[:12],
            username=username,
            password_hash=password_hash,
        )
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user.id, user.username, user.password_hash, user.created_at.isoformat()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("Tên người dùng đã tồn tại")
        return user

    def get_by_username(self, username: str) -> Optional[User]:
        """Tìm người dùng theo username."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row) if row else None

    def get(self, user_id: str) -> Optional[User]:
        """Tìm người dùng theo id."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )