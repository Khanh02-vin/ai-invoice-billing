from src.models import User
from src.store.sql_repository import Database, UserRepository


def test_list_verified_returns_only_verified_users(tmp_path):
    db = Database(str(tmp_path / "users.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add_all(
            [
                User(id="u-ok", username="ok", password_hash="x" * 60, verified=True),
                User(id="u-pending", username="pending", password_hash="y" * 60),
            ]
        )
        session.commit()

    ids = [u.id for u in UserRepository(db).list_verified()]

    assert ids == ["u-ok"]
