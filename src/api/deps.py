"""Dependency dùng chung cho mọi router: DB, repository và user hiện tại.

Chỉ một bản ``current_user`` duy nhất ở đây — trước đây mỗi router tự copy một
bản vì không import được từ ``app``.
"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.security import decode_token
from ..config import get_settings
from ..errors import AppError
from ..models import User
from ..store.sql_repository import (
    Database,
    PlaceRepository,
    SpendDraftRepository,
    SpendRepository,
    UserRepository,
    get_database,
)

bearer = HTTPBearer(auto_error=False)


def get_db() -> Database:
    return get_database(get_settings().database_path)


def user_repo() -> UserRepository:
    return UserRepository(get_db())


def place_repo() -> PlaceRepository:
    return PlaceRepository(get_db())


def spend_repo() -> SpendRepository:
    return SpendRepository(get_db())


def spend_draft_repo() -> SpendDraftRepository:
    return SpendDraftRepository(get_db())


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    """User từ JWT bearer. 401 khi thiếu hoặc token không hợp lệ."""
    if not credentials:
        raise AppError("UNAUTHORIZED", "Cần đăng nhập.", status=401)
    user_id = decode_token(credentials.credentials)
    user = user_repo().get(user_id) if user_id else None
    if not user:
        raise AppError("UNAUTHORIZED", "Token không hợp lệ.", status=401)
    return user
