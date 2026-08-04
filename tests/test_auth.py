"""Tests cho auth: hash mật khẩu, JWT, UserRepository."""
import pytest

from src.auth.security import hash_password, verify_password, create_token, decode_token
from src.store.users import UserRepository


def test_hash_and_verify():
    """Hash chuẩn và verify đúng/sai."""
    h = hash_password("matkhau123")
    assert h != "matkhau123"
    assert verify_password("matkhau123", h) is True
    assert verify_password("sai-mat-khau", h) is False


def test_hash_unique_per_password():
    """Cùng mật khẩu ra 2 hash khác nhau (salt ngẫu nhiên)."""
    assert hash_password("abc123") != hash_password("abc123")


def test_jwt_roundtrip():
    """Tạo và decode token."""
    token = create_token("user_1")
    assert decode_token(token) == "user_1"


def test_jwt_invalid():
    """Token sai trả về None."""
    assert decode_token("token.rau.tom") is None
    assert decode_token("") is None


def test_user_repository_create_and_get():
    """Tạo và tìm người dùng."""
    repo = UserRepository(db_path=":memory:")
    user = repo.create("kien", "hash123")
    assert repo.get_by_username("kien").id == user.id
    assert repo.get(user.id).username == "kien"


def test_user_repository_duplicate_username():
    """Username trùng bị chặn."""
    repo = UserRepository(db_path=":memory:")
    repo.create("kien", "hash123")
    with pytest.raises(ValueError):
        repo.create("kien", "hash456")