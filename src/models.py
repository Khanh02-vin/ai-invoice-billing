"""SQLModel models for the spending-based food discovery app.

Two domain tables — ``Place`` (shared venue directory) and ``Spend`` (a
user's recorded payment at a venue) — plus the auth tables.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlaceCategory(str, Enum):
    """Loại địa điểm — quyết định band chi tiêu mặc định."""

    cafe = "cafe"
    restaurant = "restaurant"
    drink = "drink"
    bakery = "bakery"
    bar = "bar"
    other = "other"


# --- Auth ---
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=_new_id, primary_key=True)
    username: str = Field(index=True, unique=True, min_length=3, max_length=64)
    password_hash: str = Field(min_length=60)  # bcrypt hash
    created_at: datetime = Field(default_factory=_utcnow)
    verified: bool = False
    email: str = Field(default="", max_length=254)


class OAuthAccount(SQLModel, table=True):
    """External identity linked to a local user."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)

    id: str = Field(default_factory=_new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    provider: str = Field(index=True)
    provider_account_id: str = Field(index=True)
    email: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class EmailVerification(SQLModel, table=True):
    __tablename__ = "email_verifications"

    id: str = Field(default_factory=_new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token: str = Field(unique=True, index=True)
    expires_at: str = ""
    used: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class PasswordReset(SQLModel, table=True):
    __tablename__ = "password_resets"

    id: str = Field(default_factory=_new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    token: str = Field(unique=True, index=True)
    expires_at: str = ""
    used: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: str = Field(default_factory=_new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    jti: str = Field(unique=True, index=True)
    expires_at: str = ""
    revoked: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


# --- Domain ---
class Place(SQLModel, table=True):
    """Địa điểm dùng chung: nhiều user có thể ghi chi tiêu tại cùng một quán."""

    __tablename__ = "places"

    id: str = Field(default_factory=_new_id, primary_key=True)
    name: str
    # Tên đã chuẩn hoá (lowercase, bỏ dấu, gộp khoảng trắng) — nhận diện lại cùng
    # một quán khi user ghi chi tiêu lần sau.
    normalized_name: str = Field(index=True)
    category: str = Field(default=PlaceCategory.other.value, index=True)
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    created_by: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class Spend(SQLModel, table=True):
    """Một lần chi tiền tại địa điểm.

    ``amount`` là tổng số tiền đã trả cho cả lần đi (không phải giá món), nên
    ``party_size`` đi kèm để suy ra mức chi mỗi người.
    """

    __tablename__ = "spends"

    id: str = Field(default_factory=_new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    place_id: str = Field(foreign_key="places.id", index=True)
    amount: int  # VND, tổng bill
    party_size: int = Field(default=1, ge=1)
    occurred_at: datetime = Field(default_factory=_utcnow, index=True)
    note: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def spend_per_person(self) -> int:
        return round(self.amount / max(self.party_size, 1))


class SpendDraft(SQLModel, table=True):
    """Giao dịch tạm trước khi chuyển sang app ngân hàng.

    Lưu số tiền, thông tin người nhận/quán và toạ độ lúc quét để khi quay lại
    bấm "Đã chuyển" thì ghim thành lần chi tiêu chính thức.
    """

    __tablename__ = "spend_drafts"

    id: str = Field(default_factory=_new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    amount: int
    party_size: int = Field(default=1, ge=1)
    merchant_name: str = Field(default="", max_length=120)
    bank_bin: str = Field(default="", max_length=6)
    account: str = Field(default="", max_length=32)
    place_id: str | None = Field(default=None, index=True)
    lat: float | None = None
    lon: float | None = None
    note: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


# --- Auth DTOs ---
class UserCreate(SQLModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=254)


class UserPublic(SQLModel):
    id: str
    username: str
    email: str = ""


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    verification_sent: bool = False
    verified: bool = False
