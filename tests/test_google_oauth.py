from __future__ import annotations

import os

from fastapi.testclient import TestClient


def test_google_oauth_is_disabled_without_complete_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "oauth.db"))
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    from src.config import reset_settings
    reset_settings()
    from src.app import app

    response = TestClient(app).get("/auth/google/start")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OAUTH_UNAVAILABLE"


def test_google_oauth_status_reports_enabled_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "status.db"))
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    from src.config import reset_settings
    reset_settings()
    from src.app import app

    client = TestClient(app)
    response = client.get("/auth/google/status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://testserver/auth/google/callback")
    reset_settings()
    response = client.get("/auth/google/status")
    assert response.status_code == 200
    assert response.json() == {"enabled": True}
    reset_settings()


def test_google_oauth_state_is_signed_and_expires(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "oauth-test-secret-12345678901234567890")
    from src.config import reset_settings
    reset_settings()
    from src.routers.google_oauth import _decode_state, _state

    state = _state("login")
    assert _decode_state(state)["action"] == "login"
    try:
        _decode_state(state + "tampered")
    except Exception as exc:
        assert getattr(exc, "code", None) == "INVALID_OAUTH_STATE"
    else:
        raise AssertionError("tampered OAuth state was accepted")


def test_oauth_accounts_table_is_created_for_existing_database(tmp_path):
    from src.store.sql_repository import init_database
    from src.models import OAuthAccount
    db = init_database(str(tmp_path / "legacy.db"))
    with db.get_session() as session:
        assert session.get_bind().dialect.has_table(session.connection(), "oauth_accounts")
