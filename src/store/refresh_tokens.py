"""Repository cho refresh tokens, email verification, password reset, và MFA secrets.

Lưu trữ tất cả token dưới dạng hash (token_hash) để tránh lộ token gốc khi DB bị xâm nhập.
Sử dụng SQLiteRepo cơ sở giống UserRepository."""
import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Optional

from .db import SQLiteRepo


def _hash_token(token: str) -> str:
    """Băm token bằng sha256 để lưu trữ an toàn."""
    return hashlib.sha256(token.encode()).hexdigest()


class RefreshTokenRepository(SQLiteRepo):
    """Lưu trữ refresh tokens, email verification, password reset, và MFA."""

    def _ensure_schema(self, conn: sqlite3.Connection):
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
            CREATE TABLE IF NOT EXISTS email_verifications (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mfa_secrets (
                user_id TEXT PRIMARY KEY,
                secret TEXT NOT NULL,
                enabled INTEGER DEFAULT 0,
                backup_codes TEXT,
                updated_at TEXT NOT NULL
            )
        """)

    # ---- refresh tokens ----
    def store_token(self, token: str, user_id: str, expires_at: str, family: str) -> None:
        """Lưu refresh token (đã hash)."""
        token_hash = _hash_token(token)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, revoked, created_at, family) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (token_hash, user_id, expires_at, datetime.utcnow().isoformat(), family),
            )

    def get_token(self, token: str) -> Optional[dict]:
        """Lấy thông tin token theo hash."""
        token_hash = _hash_token(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def revoke_token(self, token: str) -> bool:
        """Thu hồi một token. Trả về True nếu token tồn tại."""
        token_hash = _hash_token(token)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,)
            )
            return cur.rowcount > 0

    def revoke_all_user(self, user_id: str) -> int:
        """Thu hồi toàn bộ token của user. Trả về số token bị thu hồi."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
            return cur.rowcount

    def revoke_family(self, family: str) -> int:
        """Thu hồi toàn bộ token trong một family (phát hiện reuse)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE family = ? AND revoked = 0",
                (family,),
            )
            return cur.rowcount

    # ---- email verification ----
    def store_email_verification(self, token: str, user_id: str, expires_at: str) -> None:
        token_hash = _hash_token(token)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO email_verifications (token_hash, user_id, expires_at, used, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (token_hash, user_id, expires_at, datetime.utcnow().isoformat()),
            )

    def verify_email(self, token: str) -> Optional[str]:
        """Đánh dấu token đã dùng và trả về user_id nếu hợp lệ."""
        token_hash = _hash_token(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_verifications WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if not row:
                return None
            if row["used"]:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.utcnow() > expires_at:
                return None
            conn.execute(
                "UPDATE email_verifications SET used = 1 WHERE token_hash = ?", (token_hash,)
            )
            return row["user_id"]

    # ---- password reset ----
    def store_password_reset(self, token: str, user_id: str, expires_at: str) -> None:
        token_hash = _hash_token(token)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO password_resets (token_hash, user_id, expires_at, used, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (token_hash, user_id, expires_at, datetime.utcnow().isoformat()),
            )

    def verify_password_reset(self, token: str) -> Optional[str]:
        """Kiểm tra token hợp lệ và đánh dấu đã dùng."""
        token_hash = _hash_token(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM password_resets WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if not row:
                return None
            if row["used"]:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.utcnow() > expires_at:
                return None
            conn.execute(
                "UPDATE password_resets SET used = 1 WHERE token_hash = ?", (token_hash,)
            )
            return row["user_id"]

    # ---- MFA ----
    def store_mfa_secret(self, user_id: str, secret: str, backup_codes: list[str]) -> None:
        """Lưu secret MFA (chưa enable)."""
        codes_json = json.dumps(backup_codes)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mfa_secrets (user_id, secret, enabled, backup_codes, updated_at) "
                "VALUES (?, ?, 0, ?, ?)",
                (user_id, secret, codes_json, datetime.utcnow().isoformat()),
            )

    def get_mfa_secret(self, user_id: str) -> Optional[dict]:
        """Lấy thông tin MFA của user."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mfa_secrets WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["backup_codes"] = json.loads(result["backup_codes"]) if result["backup_codes"] else []
        return result

    def enable_mfa(self, user_id: str) -> bool:
        """Bật MFA sau khi verify thành công."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE mfa_secrets SET enabled = 1, updated_at = ? WHERE user_id = ?",
                (datetime.utcnow().isoformat(), user_id),
            )
            return cur.rowcount > 0

    def disable_mfa(self, user_id: str) -> bool:
        """Tắt MFA."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE mfa_secrets SET enabled = 0, updated_at = ? WHERE user_id = ?",
                (datetime.utcnow().isoformat(), user_id),
            )
            return cur.rowcount > 0

    def consume_backup_code(self, user_id: str, code: str) -> bool:
        """Tiêu thụ một backup code (dùng 1 lần)."""
        info = self.get_mfa_secret(user_id)
        if not info or not info["backup_codes"]:
            return False
        codes = info["backup_codes"]
        if code not in codes:
            return False
        codes.remove(code)
        with self._connect() as conn:
            conn.execute(
                "UPDATE mfa_secrets SET backup_codes = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(codes), datetime.utcnow().isoformat(), user_id),
            )
        return True
