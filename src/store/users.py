"""Repository người dùng trên SQLite."""
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..domain.models import User
from .db import SQLiteRepo


class UserRepository(SQLiteRepo):
    """Lưu trử người dùng (bảng users)."""

    def _ensure_schema(self, conn: sqlite3.Connection):
        # Migration: drop legacy plaintext tables (jti/token) if present.
        # Hashed schema is created lazily by token methods + RefreshTokenRepository.
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='refresh_tokens'").fetchone()
            if row and row[0] and "jti TEXT" in row[0]:
                conn.execute("DROP TABLE refresh_tokens")
        except Exception:
            pass
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='email_verifications'").fetchone()
            if row and row[0] and "token TEXT" in row[0]:
                conn.execute("DROP TABLE email_verifications")
        except Exception:
            pass
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'").fetchone()
            if row and row[0]:
                conn.execute("DROP TABLE password_reset_tokens")
        except Exception:
            pass
        # Legacy password_resets with token column also needs drop (rare)
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='password_resets'").fetchone()
            if row and row[0] and "token TEXT" in row[0]:
                conn.execute("DROP TABLE password_resets")
        except Exception:
            pass

        """Tạo bảng users + bảng auth hardening nếu chưa tồn tại."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT,
                verified INTEGER DEFAULT 0,
                mfa_enabled INTEGER DEFAULT 0,
                email TEXT DEFAULT ''
            )
        """)
        # Migration: thêm cột email cho DB cũ (dùng cho IMAP routing + email push)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # cột đã có
        # MFA TOTP secrets (one per user).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mfa_secrets (
                user_id TEXT PRIMARY KEY,
                secret TEXT NOT NULL,
                enabled INTEGER DEFAULT 0,
                backup_codes TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        # Note: email_verifications, password_resets, refresh_tokens
        # are managed by RefreshTokenRepository (hashed token system).

    def create(self, username: str, password_hash: str, email: str = "") -> User:
        """Tạo người dùng mới. Raise ValueError nếu username đã tồn tại."""
        user = User(
            id=hashlib.md5(username.encode()).hexdigest()[:12],
            username=username,
            password_hash=password_hash,
            email=email or "",
        )
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at, email) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user.id, user.username, user.password_hash, user.created_at.isoformat(), user.email),
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

    def list_verified(self) -> list["User"]:
        """Danh sách user đã xác minh email — dùng cho IMAP poller gán giao dịch."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users WHERE verified = 1").fetchall()
        return [self._row_to_user(r) for r in rows]

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            verified=bool(row["verified"]) if "verified" in row.keys() else False,
            mfa_enabled=bool(row["mfa_enabled"]) if "mfa_enabled" in row.keys() else False,
            email=row["email"] if "email" in row.keys() else "",
        )

    def hard_delete(self, user_id: str) -> bool:
        """Xóa vĩnh viễn người dùng (GDPR right to erasure). Trả về True nếu đã xóa."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cur.rowcount > 0

    def update_password(self, user_id: str, new_hash: str) -> bool:
        """Cập nhật mật khẩu. Trả về True nếu user tồn tại."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, user_id),
            )
            return cur.rowcount > 0

    def set_verified(self, user_id: str, verified: bool = True) -> None:
        """Đặt trạng thái verified cho user (tiện cho test/admin)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET verified = ? WHERE id = ?",
                (1 if verified else 0, user_id),
            )

    # ---- MFA ----

    def store_mfa_secret(self, user_id: str, secret: str) -> None:
        """Lưu secret TOTP."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mfa_secrets (user_id, secret, enabled, backup_codes, updated_at)
                VALUES (?, ?, 0, NULL, ?)
                ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret, updated_at=excluded.updated_at
                """,
                (user_id, secret, datetime.now(timezone.utc).isoformat()),
            )

    def get_mfa_secret(self, user_id: str) -> Optional[dict]:
        """Lấy secret TOTP."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mfa_secrets WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["enabled"] = bool(d["enabled"])
        return d

    def set_mfa_enabled(self, user_id: str, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET mfa_enabled = ? WHERE id = ?",
                (1 if enabled else 0, user_id),
            )
            conn.execute(
                "UPDATE mfa_secrets SET enabled = ? WHERE user_id = ?",
                (1 if enabled else 0, user_id),
            )

    # ---- Auth token shims (delegated to hashed tables in the unified schema) ----
    def _hash_token(self, token: str) -> str:
        import hashlib as _h
        return _h.sha256(token.encode()).hexdigest()

    def create_email_verification(self, user_id: str, token: str, expires_at: str) -> None:
        token_hash = self._hash_token(token)
        with self._connect() as conn:
            # Ensure hashed tables exist even when this repo is the only one initialized.
            # Keep in sync with src/store/refresh_tokens.py schema (hashed).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_verifications (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    family TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                """INSERT INTO email_verifications (token_hash, user_id, expires_at, used, created_at)
                   VALUES (?, ?, ?, 0, ?)""",
                (token_hash, user_id, expires_at, datetime.now(timezone.utc).isoformat()),
            )

    def consume_email_verification(self, token: str) -> "Optional[str]":
        token_hash = self._hash_token(token)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM email_verifications WHERE token_hash = ?", (token_hash,)).fetchone()
            if not row:
                return None
            if row["used"]:
                return None
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                return None
            conn.execute("UPDATE email_verifications SET used = 1 WHERE token_hash = ?", (token_hash,))
            conn.execute("UPDATE users SET verified = 1 WHERE id = ?", (row["user_id"],))
            return row["user_id"]

    def create_password_reset(self, user_id: str, token: str, expires_at: str) -> None:
        token_hash = self._hash_token(token)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                """INSERT INTO password_resets (token_hash, user_id, expires_at, used, created_at)
                   VALUES (?, ?, ?, 0, ?)""",
                (token_hash, user_id, expires_at, datetime.now(timezone.utc).isoformat()),
            )

    def consume_password_reset(self, token: str) -> "Optional[str]":
        token_hash = self._hash_token(token)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM password_resets WHERE token_hash = ?", (token_hash,)).fetchone()
            if not row:
                return None
            if row["used"]:
                return None
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                return None
            conn.execute("UPDATE password_resets SET used = 1 WHERE token_hash = ?", (token_hash,))
            return row["user_id"]

    def store_refresh_token(self, jti: str, user_id: str, expires_at: str, family: str = "") -> None:
        token_hash = self._hash_token(jti)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    family TEXT NOT NULL
                )
            """)
            conn.execute(
                """INSERT INTO refresh_tokens (token_hash, user_id, expires_at, revoked, created_at, family)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (token_hash, user_id, expires_at, datetime.now(timezone.utc).isoformat(), family),
            )

    def is_refresh_token_valid(self, jti: str) -> "Optional[str]":
        token_hash = self._hash_token(jti)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
            if not row:
                return None
            if row["revoked"]:
                return None
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                return None
            return row["user_id"]

    def revoke_refresh_token(self, jti: str) -> None:
        token_hash = self._hash_token(jti)
        with self._connect() as conn:
            conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,))

