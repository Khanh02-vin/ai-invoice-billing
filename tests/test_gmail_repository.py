from datetime import UTC, datetime, timedelta

from src.models import User
from src.store.sql_repository import Database, GmailRepository


def _repo(tmp_path):
    db = Database(str(tmp_path / "gmail.db"))
    db.create_tables()
    with db.get_session() as session:
        session.add_all(
            [
                User(id="user-a", username="user-a", password_hash="x" * 60),
                User(id="user-b", username="user-b", password_hash="y" * 60),
            ]
        )
        session.commit()
    return GmailRepository(db)


def test_gmail_connection_cursor_watch_and_alert_dedup(tmp_path):
    repo = _repo(tmp_path)
    connection = repo.create_connection(
        "user-a",
        "google-sub-a",
        email="a@gmail.com",
        encrypted_refresh_token="encrypted",
    )
    assert repo.create_connection("user-a", "google-sub-a").id == connection.id

    expiration = datetime.now(UTC) + timedelta(hours=1)
    updated = repo.update_watch(
        connection.id,
        "user-a",
        history_id="history-1",
        watch_expiration=expiration,
        watch_topic="projects/p/topics/gmail",
    )
    assert updated.history_id == "history-1"
    assert updated.watch_topic.endswith("gmail")

    first = repo.upsert_payment_alert(
        "user-a",
        connection.id,
        "message-1",
        subject="Paid",
        raw_payload={"id": "message-1"},
    )
    duplicate = repo.upsert_payment_alert(
        "user-a", connection.id, "message-1", subject="Updated"
    )
    assert duplicate.id == first.id
    assert len(repo.list_payment_alerts(connection.id, "user-a")) == 1
    assert repo.get_payment_alert(first.id, "user-b") is None


def test_gmail_repository_enforces_ownership_and_disconnect_cleanup(tmp_path):
    repo = _repo(tmp_path)
    connection = repo.create_connection("user-a", "google-sub-a")
    alert = repo.upsert_payment_alert("user-a", connection.id, "message-1")

    assert repo.get_connection(connection.id, "user-b") is None
    assert repo.update_cursor(connection.id, "user-b", "should-not-write") is None
    assert repo.clear_credentials(connection.id) is True
    cleared = repo.get_connection(connection.id, "user-a")
    assert cleared.encrypted_refresh_token == ""
    assert cleared.history_id is None
    assert cleared.watch_expiration is None
    assert repo.upsert_payment_alert("user-b", connection.id, "message-2") is None
    assert repo.disconnect(connection.id, "user-b") is False
    assert repo.disconnect(connection.id, "user-a") is True
    assert repo.get_connection(connection.id, "user-a") is None
    assert repo.get_payment_alert(alert.id, "user-a") is None
