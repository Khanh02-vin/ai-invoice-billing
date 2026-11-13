"""Repositories: auth, địa điểm và lần chi tiêu.

Bảng ``places`` dùng chung cho mọi user; ``spends`` chỉ thuộc về user tạo ra nó.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from src.discovery import percentile
from src.geo import haversine_km
from src.models import (
    EmailVerification,
    OAuthAccount,
    PasswordReset,
    Place,
    RefreshToken,
    Spend,
    SpendDraft,
    User,
)
from src.time_utils import as_utc

logger = logging.getLogger(__name__)

# Hai lần ghi cùng tên trong bán kính này là cùng một quán.
SAME_PLACE_RADIUS_KM = 0.05


class Database:
    """Database engine and session management."""

    def __init__(self, db_path: str = "data/spends.db"):
        self.db_path = db_path
        # SQLite with WAL mode for concurrent reads
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 10},
            echo=False,
        )
        # Enable WAL mode
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA busy_timeout=5000")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    def create_tables(self):
        SQLModel.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return Session(self.engine)

    def ping(self) -> None:
        """Readiness probe: DB mở và trả lời được."""
        with self.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")


class UserRepository:
    """User repository with SQLModel."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, username: str, password_hash: str, email: str = "") -> User:
        user = User(username=username, password_hash=password_hash, email=email)
        try:
            with self.db.get_session() as session:
                session.add(user)
                session.commit()
                session.refresh(user)
        except IntegrityError as exc:  # username đã tồn tại
            raise ValueError("USERNAME_EXISTS") from exc
        return user

    def get(self, user_id: str) -> User | None:
        with self.db.get_session() as session:
            return session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        with self.db.get_session() as session:
            return session.exec(select(User).where(User.username == username)).first()

    def get_by_email(self, email: str) -> User | None:
        """Find a local account by normalized email without linking identities."""
        normalized = email.strip().casefold()
        if not normalized:
            return None
        with self.db.get_session() as session:
            return session.exec(
                select(User).where(func.lower(User.email) == normalized)
            ).first()

    def get_oauth_account(self, provider: str, provider_account_id: str) -> OAuthAccount | None:
        with self.db.get_session() as session:
            return session.exec(
                select(OAuthAccount).where(
                    OAuthAccount.provider == provider,
                    OAuthAccount.provider_account_id == provider_account_id,
                )
            ).first()

    def list_oauth_accounts(self, user_id: str) -> list[OAuthAccount]:
        with self.db.get_session() as session:
            return session.exec(select(OAuthAccount).where(OAuthAccount.user_id == user_id)).all()

    def link_oauth_account(self, user_id: str, provider: str, provider_account_id: str, email: str) -> OAuthAccount:
        account = OAuthAccount(
            user_id=user_id,
            provider=provider,
            provider_account_id=provider_account_id,
            email=email,
        )
        with self.db.get_session() as session:
            session.add(account)
            session.commit()
            session.refresh(account)
        return account

    def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        with self.db.get_session() as session:
            account = session.exec(
                select(OAuthAccount).where(
                    OAuthAccount.user_id == user_id, OAuthAccount.provider == provider
                )
            ).first()
            if not account:
                return False
            session.delete(account)
            session.commit()
            return True

    def update_password(self, user_id: str, password_hash: str) -> bool:
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if not user:
                return False
            user.password_hash = password_hash
            session.add(user)
            session.commit()
            return True

    def store_refresh_token(self, jti: str, user_id: str, expires_at: str):
        token = RefreshToken(jti=jti, user_id=user_id, expires_at=expires_at)
        with self.db.get_session() as session:
            session.add(token)
            session.commit()

    def is_refresh_token_valid(self, jti: str) -> str | None:
        with self.db.get_session() as session:
            token = session.exec(select(RefreshToken).where(RefreshToken.jti == jti)).first()
            if token and not token.revoked and token.expires_at and as_utc(datetime.fromisoformat(token.expires_at)) > datetime.now(UTC):
                return token.user_id
            return None

    def revoke_refresh_token(self, jti: str):
        with self.db.get_session() as session:
            token = session.exec(select(RefreshToken).where(RefreshToken.jti == jti)).first()
            if token:
                token.revoked = True
                session.add(token)
                session.commit()

    def set_verified(self, user_id: str, verified: bool = True):
        with self.db.get_session() as session:
            user = session.get(User, user_id)
            if user:
                user.verified = verified
                session.add(user)
                session.commit()

    def list_verified(self) -> list[User]:
        with self.db.get_session() as session:
            return list(
                session.exec(select(User).where(User.verified == True)).all()  # noqa: E712
            )

    def create_email_verification(self, user_id: str, token: str, expires_at: str):
        ev = EmailVerification(user_id=user_id, token=token, expires_at=expires_at)
        with self.db.get_session() as session:
            session.add(ev)
            session.commit()

    def consume_email_verification(self, token: str) -> str | None:
        with self.db.get_session() as session:
            ev = session.exec(
                select(EmailVerification).where(EmailVerification.token == token)
            ).first()
            if ev and not ev.used and ev.expires_at and as_utc(datetime.fromisoformat(ev.expires_at)) > datetime.now(UTC):
                ev.used = True
                user_id = ev.user_id
                session.add(ev)
                session.commit()
                # Mark user as verified
                user = session.get(User, user_id)
                if user:
                    user.verified = True
                    session.add(user)
                    session.commit()
                return user_id
            return None

    def create_password_reset(self, user_id: str, token: str, expires_at: str):
        pr = PasswordReset(user_id=user_id, token=token, expires_at=expires_at)
        with self.db.get_session() as session:
            session.add(pr)
            session.commit()

    def consume_password_reset(self, token: str) -> str | None:
        with self.db.get_session() as session:
            pr = session.exec(select(PasswordReset).where(PasswordReset.token == token)).first()
            if pr and not pr.used and pr.expires_at and as_utc(datetime.fromisoformat(pr.expires_at)) > datetime.now(UTC):
                pr.used = True
                user_id = pr.user_id
                session.add(pr)
                session.commit()
                return user_id
            return None


@dataclass(frozen=True)
class PlaceStats:
    """Mức chi ghi nhận của *một user* tại một địa điểm."""

    place: Place
    visits: int
    median_spend: int
    last_visit: datetime


class PlaceRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, place_id: str) -> Place | None:
        with self.db.get_session() as session:
            return session.get(Place, place_id)

    def create(self, *, name: str, normalized_name: str, category: str, address: str = "",
               lat: float | None = None, lon: float | None = None,
               created_by: str | None = None) -> Place:
        place = Place(
            name=name,
            normalized_name=normalized_name,
            category=category,
            address=address,
            lat=lat,
            lon=lon,
            created_by=created_by,
        )
        with self.db.get_session() as session:
            session.add(place)
            session.commit()
            session.refresh(place)
        return place

    def find_match(self, normalized_name: str, lat: float | None, lon: float | None) -> Place | None:
        """Cùng tên và cách nhau ≤50 m → cùng một quán.

        Không có toạ độ ở một trong hai bên thì không đo được khoảng cách; chỉ
        coi là trùng khi cả hai đều không có toạ độ (tránh gộp nhầm hai quán
        cùng tên ở hai nơi khác nhau).
        """
        with self.db.get_session() as session:
            candidates = session.exec(
                select(Place).where(Place.normalized_name == normalized_name)
            ).all()
        for place in candidates:
            if place.lat is None or place.lon is None or lat is None or lon is None:
                same_spot = place.lat is None and place.lon is None and lat is None and lon is None
            else:
                same_spot = haversine_km(place.lat, place.lon, lat, lon) <= SAME_PLACE_RADIUS_KM
            if same_spot:
                return place
        return None

    def search(self, q: str = "", category: str = "") -> list[Place]:
        statement = select(Place)
        if q:
            statement = statement.where(Place.normalized_name.contains(q))
        if category:
            statement = statement.where(Place.category == category)
        with self.db.get_session() as session:
            return list(session.exec(statement.order_by(Place.name)).all())


class SpendRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, user_id: str, place_id: str, amount: int, party_size: int = 1,
               occurred_at: datetime | None = None, note: str = "") -> Spend:
        spend = Spend(
            user_id=user_id,
            place_id=place_id,
            amount=amount,
            party_size=party_size,
            occurred_at=occurred_at or datetime.now(UTC),
            note=note,
        )
        with self.db.get_session() as session:
            session.add(spend)
            session.commit()
            session.refresh(spend)
        return spend

    def get(self, spend_id: str) -> Spend | None:
        with self.db.get_session() as session:
            return session.get(Spend, spend_id)

    def list(self, user_id: str, start: datetime | None = None,
             end: datetime | None = None) -> list[Spend]:
        statement = select(Spend).where(Spend.user_id == user_id)
        if start is not None:
            statement = statement.where(Spend.occurred_at >= start)
        if end is not None:
            statement = statement.where(Spend.occurred_at <= end)
        with self.db.get_session() as session:
            return list(session.exec(statement.order_by(Spend.occurred_at.desc())).all())

    def delete(self, user_id: str, spend_id: str) -> bool:
        """Chỉ người tạo xoá được; người khác nhận ``False`` như không tìm thấy."""
        with self.db.get_session() as session:
            spend = session.get(Spend, spend_id)
            if spend is None or spend.user_id != user_id:
                return False
            session.delete(spend)
            session.commit()
            return True

    def per_place(self, user_id: str) -> dict[str, PlaceStats]:
        """Gom lịch sử chi tiêu của user theo địa điểm."""
        with self.db.get_session() as session:
            spends = session.exec(select(Spend).where(Spend.user_id == user_id)).all()
            places = {p.id: p for p in session.exec(select(Place)).all()}
        grouped: dict[str, list[Spend]] = {}
        for spend in spends:
            if spend.place_id in places:
                grouped.setdefault(spend.place_id, []).append(spend)
        stats: dict[str, PlaceStats] = {}
        for place_id, rows in grouped.items():
            values = [row.spend_per_person for row in rows]
            stats[place_id] = PlaceStats(
                place=places[place_id],
                visits=len(rows),
                median_spend=percentile(values, 0.5),
                last_visit=max(row.occurred_at for row in rows),
            )
        return stats

    def category_visits(self, user_id: str) -> dict[str, int]:
        """Số lần ghi chi tiêu theo loại địa điểm — dùng cho thành phần C."""
        with self.db.get_session() as session:
            spends = session.exec(select(Spend).where(Spend.user_id == user_id)).all()
            places = {p.id: p.category for p in session.exec(select(Place)).all()}
        counts: dict[str, int] = {}
        for spend in spends:
            category = places.get(spend.place_id)
            if category:
                counts[category] = counts.get(category, 0) + 1
        return counts


class SpendDraftRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, user_id: str, amount: int, merchant_name: str = "", bank_bin: str = "",
               account: str = "", place_id: str | None = None, lat: float | None = None,
               lon: float | None = None, note: str = "", party_size: int = 1) -> SpendDraft:
        draft = SpendDraft(
            user_id=user_id,
            amount=amount,
            party_size=party_size,
            merchant_name=merchant_name,
            bank_bin=bank_bin,
            account=account,
            place_id=place_id,
            lat=lat,
            lon=lon,
            note=note,
        )
        with self.db.get_session() as session:
            session.add(draft)
            session.commit()
            session.refresh(draft)
        return draft

    def list(self, user_id: str) -> list[SpendDraft]:
        with self.db.get_session() as session:
            return list(
                session.exec(
                    select(SpendDraft)
                    .where(SpendDraft.user_id == user_id)
                    .order_by(SpendDraft.created_at.desc())
                ).all()
            )

    def get(self, draft_id: str) -> SpendDraft | None:
        with self.db.get_session() as session:
            return session.get(SpendDraft, draft_id)

    def delete(self, user_id: str, draft_id: str) -> bool:
        with self.db.get_session() as session:
            draft = session.get(SpendDraft, draft_id)
            if draft is None or draft.user_id != user_id:
                return False
            session.delete(draft)
            session.commit()
            return True


def _ensure_model_columns(db: Database) -> None:
    """Bảng cũ thiếu cột mà model mới khai báo — ``create_all`` chỉ tạo bảng mới,
    không ALTER bảng có sẵn, nên SELECT của SQLModel lỗi "no such column". Thêm
    mọi cột còn thiếu dạng nullable: SQLite không cho thêm cột NOT NULL vào bảng
    đã có dữ liệu khi không có DEFAULT; insert qua SQLModel vẫn ghi đủ giá trị."""
    dialect = db.engine.dialect
    with db.engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            have = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
            if not have:
                continue
            for col in table.columns:
                if col.name in have:
                    continue
                if not col.nullable:
                    logger.warning(
                        "Cột %s.%s khai báo NOT NULL nhưng DB cũ chưa có; "
                        "thêm tạm dạng nullable.",
                        table.name,
                        col.name,
                    )
                ddl = f"{col.name} {col.type.compile(dialect=dialect)}"
                if col.server_default is not None:
                    ddl += f" DEFAULT {col.server_default.arg}"
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")


def _ensure_auth_token_tables(db: Database) -> None:
    """DB cũ tạo bảng token bằng raw SQL (khoá token_hash); repository hiện tại
    dùng SQLModel (id + token/jti) nên bảng legacy không ghi được. Token chỉ là
    phiên tạm, tạo lại theo model là an toàn (người dùng đăng nhập lại)."""
    for model in (RefreshToken, EmailVerification, PasswordReset):
        table = model.__tablename__
        with db.engine.begin() as conn:
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if cols and "id" not in cols:
                conn.exec_driver_sql(f"DROP TABLE {table}")
                model.__table__.create(bind=conn)


_database: Database | None = None


def get_database(db_path: str = "data/spends.db") -> Database:
    global _database
    if _database is None or _database.db_path != db_path:
        _database = init_database(db_path)
    return _database


def init_database(db_path: str = "data/spends.db") -> Database:
    global _database
    _database = Database(db_path)
    _database.create_tables()
    # Token tables legacy phải drop/tạo lại TRƯỚC khi thêm cột thiếu, nếu không
    # ALTER sẽ giữ lại các cột NOT NULL cũ (family, token_hash) mà model không ghi.
    _ensure_auth_token_tables(_database)
    _ensure_model_columns(_database)
    return _database
