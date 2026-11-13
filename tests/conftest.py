"""Pytest conftest — test KHÔNG BAO GIỜ chạm DB dev.

Mỗi test dùng một file SQLite trong ``tmp_path`` (không dùng ``:memory:``: pool
của SQLAlchemy là per-thread còn TestClient chạy app ở thread khác).
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="spends-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "spends.db")

import pytest


@pytest.fixture(autouse=True)
def safe_db(tmp_path, monkeypatch):
    """Mỗi test một DB tạm riêng — không đụng file thật; pytest tự xoá tmp_path."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("APP_URL", "http://testserver")
    yield
    # Clear rate-limit singleton để các test không bị ảnh hưởng bởi test trước.
    try:
        from src.security.rate_limit import get_default_limiter

        get_default_limiter().clear()
    except Exception:
        pass


@pytest.fixture
def client():
    """TestClient trên app thật; DB là file tạm mà ``safe_db`` đặt cho từng test."""
    from fastapi.testclient import TestClient

    from src.app import app

    return TestClient(app)


@pytest.fixture
def make_user():
    """Tạo user trực tiếp trong DB test và header Bearer tương ứng.

    Không đi qua ``/auth/register`` để test không phụ thuộc OPEN_REGISTRATION
    và không ăn hạn mức rate-limit.
    """
    from src.api.deps import user_repo
    from src.auth.security import create_token, hash_password

    def _make(username: str):
        user = user_repo().create(username, hash_password("matkhau123"))
        return user, {"Authorization": f"Bearer {create_token(user.id)}"}

    return _make
