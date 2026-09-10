"""Refresh token, revocation, và rotation.

Token là opaque random string (không chứa dữ liệu), lưu hash trong DB.
Rotation: mỗi lần refresh, token cũ bị thu hồi và cấp token mới cùng family.
Nếu token đã thu hồi bị dùng lại -> thu hồi toàn bộ family (phát hiện theft)."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from ..store.refresh_tokens import RefreshTokenRepository
from .security import create_token

# Module-level repo (mặc định file DB). Test có thể tạo instance riêng :memory:.
refresh_repo = RefreshTokenRepository()


def create_refresh_token(
    user_id: str,
    days: int = 30,
    repo: Optional[RefreshTokenRepository] = None,
    family: Optional[str] = None,
) -> Tuple[str, str]:
    """Tạo refresh token opaque, lưu hash. Trả về (token, family).

    Nếu family được cung cấp, token sẽ thuộc family đó (dùng khi rotation)."""
    r = repo or refresh_repo
    token = secrets.token_urlsafe(48)
    if family is None:
        family = secrets.token_urlsafe(16)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    r.store_token(token, user_id, expires_at, family)
    return token, family


def verify_refresh_token(token: str, repo: Optional[RefreshTokenRepository] = None) -> Optional[str]:
    """Kiểm tra refresh token hợp lệ (chưa thu hồi, chưa hết hạn). Trả về user_id."""
    r = repo or refresh_repo
    info = r.get_token(token)
    if not info:
        return None
    if info["revoked"]:
        return None
    expires_at = datetime.fromisoformat(info["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        return None
    return info["user_id"]


def revoke_token(token: str, repo: Optional[RefreshTokenRepository] = None) -> bool:
    """Thu hồi một token. Trả về True nếu token tồn tại."""
    r = repo or refresh_repo
    return r.revoke_token(token)


def revoke_all_user_tokens(user_id: str, repo: Optional[RefreshTokenRepository] = None) -> int:
    """Thu hồi toàn bộ refresh token của user. Trả về số token bị thu hồi."""
    r = repo or refresh_repo
    return r.revoke_all_user(user_id)


def refresh_access_token(
    refresh_token: str,
    repo: Optional[RefreshTokenRepository] = None,
) -> Optional[Tuple[str, str]]:
    """Rotation: xác minh refresh token, thu hồi cũ, cấp access token + refresh token mới.

    Nếu token đã bị thu hồi -> có thể là reuse attack -> thu hồi toàn bộ family.
    Trả về (access_token, new_refresh_token) hoặc None."""
    r = repo or refresh_repo
    info = r.get_token(refresh_token)
    if not info:
        return None

    user_id = info["user_id"]
    family = info["family"]

    if info["revoked"]:
        # Token đã thu hồi mà vẫn được dùng -> reuse attack. Thu hồi toàn family.
        r.revoke_family(family)
        return None

    expires_at = datetime.fromisoformat(info["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        return None

    # Thu hồi token hiện tại.
    r.revoke_token(refresh_token)

    # Cấp token mới cùng family (rotation).
    new_refresh, _ = create_refresh_token(user_id, days=30, repo=r, family=family)
    access = create_token(user_id, days=7)
    return access, new_refresh
