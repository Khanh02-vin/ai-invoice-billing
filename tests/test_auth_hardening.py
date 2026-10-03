"""Tests for Phase 4 auth hardening: email verification, password reset, refresh tokens, MFA."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.security import (
    create_email_token,
    hash_token,
    verify_totp,
    generate_backup_codes,
    create_token,
    decode_token,
)
from src.store.users import UserRepository
from src.routers.auth_ext import router as auth_ext_router


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def monkeypatch_env(monkeypatch):
    """Force open registration and patch all repos to use in-memory user_repo."""
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    monkeypatch.setenv("JWT_SECRET", "test-secret-1234567890-1234567890")
    from src.config import reset_settings
    reset_settings()
    yield


@pytest.fixture
def user_repo():
    return UserRepository(db_path=":memory:")


@pytest.fixture
def client(user_repo):
    """Test client using full app (login/register are in app.py)."""
    # Patch the module-level repos to use our in-memory repo.
    import src.routers.auth_ext as auth_ext_module
    auth_ext_module.user_repo = user_repo
    import src.app as app_module
    app_module.users = user_repo
    # Reset settings cache so env vars take effect
    from src.config import reset_settings
    reset_settings()
    return TestClient(app_module.app)


def _register(client, username="kien", password="matkhau123", email=None):
    """Helper: register, return access token. Không gửi email xác minh trong tests
    (SMTP chưa bật) — để riêng test_email_verification_flow kiểm tra gửi thật."""
    payload = {"username": username, "password": password}
    if email is not None:
        payload["email"] = email
    r = client.post("/auth/register", json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()["access_token"]


def _login(client, username="kien", password="matkhau123"):
    """Helper: login, return access token."""
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---- Tests: Email verification ----

def test_email_endpoints_fail_closed_without_smtp(client, user_repo):
    """Existing and unknown users receive identical 503 responses offline."""
    _register(client)
    responses = [
        client.post("/auth/resend-verification", json={"username": "kien"}),
        client.post("/auth/resend-verification", json={"username": "unknown"}),
        client.post("/auth/forgot-password", json={"username": "kien"}),
        client.post("/auth/forgot-password", json={"username": "unknown"}),
    ]
    for response in responses:
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "EMAIL_SERVICE_UNAVAILABLE"
        assert not any(key in body for key in ("token", "reset_token", "verification_token"))


def test_email_verification_flow(client, user_repo):
    """Directly create a token, then verify and reject replay."""
    _register(client)
    user = user_repo.get_by_username("kien")
    verify_token = create_email_token()
    user_repo.create_email_verification(
        user.id, verify_token, (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    )

    r = client.post("/auth/verify-email", json={"token": verify_token})
    assert r.status_code == 200
    assert "thanh cong" in r.json()["message"]

    # Token is single-use: replay should fail.
    r = client.post("/auth/verify-email", json={"token": verify_token})
    assert r.status_code == 400


# ---- Tests: Password reset ----

def test_register_sends_verification_email(monkeypatch, client, user_repo):
    """Register + SMTP bật -> email xác minh gửi thật; link trong email verify được user."""
    import re

    sent: list = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def ehlo(self): pass
        def login(self, *a, **k): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_SMTP_USER", "noreply@test")
    monkeypatch.setenv("EMAIL_FROM", "noreply@test")
    monkeypatch.setattr("src.mail.smtplib.SMTP", FakeSMTP)

    r = client.post("/auth/register", json={
        "username": "newuser", "password": "matkhau123", "email": "new@example.com"
    })
    assert r.status_code == 200
    assert r.json()["verification_sent"] is True
    assert user_repo.get_by_username("newuser").verified is False
    assert len(sent) == 1
    assert sent[0]["To"] == "new@example.com"

    m = re.search(r"/auth/verify-email/confirm\?token=([\w\-]+)", sent[0].get_content())
    assert m, sent[0].get_content()
    assert client.get(f"/auth/verify-email/confirm?token={m.group(1)}").status_code == 200
    assert user_repo.get_by_username("newuser").verified is True
    # Link dùng 1 lần
    assert client.get(f"/auth/verify-email/confirm?token={m.group(1)}").status_code == 400


def test_register_without_smtp_reports_not_sent(client, user_repo):
    """SMTP chưa cấu hình -> verification_sent False để frontend báo user liên hệ admin."""
    r = client.post("/auth/register", json={
        "username": "nosmtp", "password": "matkhau123", "email": "n@example.com"
    })
    assert r.status_code == 200
    assert r.json()["verification_sent"] is False
    assert user_repo.get_by_username("nosmtp").verified is False


def test_forgot_password_sends_reset_email(monkeypatch, client, user_repo):
    """SMTP cấu hình -> forgot-password gửi email chứa token reset dùng được thật."""
    import re

    sent: list = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def ehlo(self): pass
        def login(self, *a, **k): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_FROM", "noreply@test")
    monkeypatch.setattr("src.mail.smtplib.SMTP", FakeSMTP)

    _register(client, email="kien@example.com")  # gửi luôn email xác minh
    sent.clear()

    r = client.post("/auth/forgot-password", json={"username": "kien"})
    assert r.status_code == 200
    assert len(sent) == 1
    assert sent[0]["To"] == "kien@example.com"
    body = sent[0].get_content()
    m = re.search(r"mật khẩu mới:\s*\n\s*\n(\S+)", body)
    assert m, body

    # Token trong email dùng reset được thật -> login bằng mật khẩu mới
    r = client.post("/auth/reset-password", json={"token": m.group(1), "new_password": "matsau123"})
    assert r.status_code == 200, r.text
    user_repo.set_verified(user_repo.get_by_username("kien").id, True)
    assert client.post("/auth/login", json={"username": "kien", "password": "matsau123"}).status_code == 200


def test_resend_verification_sends_and_reports_failure(monkeypatch, client, user_repo):
    """Resend gửi thật khi SMTP cấu hình; SMTP lỗi -> 502 (không báo 'đã gửi' giả)."""
    _register(client, email="kien@example.com")  # chưa bật SMTP -> không gửi

    sent: list = []

    class GoodSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def ehlo(self): pass
        def login(self, *a, **k): pass
        def send_message(self, msg): sent.append(msg)

    class DeadSMTP(GoodSMTP):
        def send_message(self, msg): raise OSError("smtp down")

    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_SMTP_USER", "noreply@test")
    monkeypatch.setenv("EMAIL_FROM", "noreply@test")

    monkeypatch.setattr("src.mail.smtplib.SMTP", GoodSMTP)
    r = client.post("/auth/resend-verification", json={"username": "kien"})
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    assert sent[0]["To"] == "kien@example.com"

    monkeypatch.setattr("src.mail.smtplib.SMTP", DeadSMTP)
    r = client.post("/auth/resend-verification", json={"username": "kien"})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "EMAIL_SEND_FAILED"

def test_password_reset_flow(client, user_repo):
    """Forgot password -> reset -> login with new password."""
    # Ensure user exists.
    _register(client)
    user_repo.set_verified(user_repo.get_by_username("kien").id, True)

    # Create the reset token through the repository, not an HTTP response.
    reset_token = create_email_token()
    user_repo.create_password_reset(
        user_repo.get_by_username("kien").id,
        reset_token,
        (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )

    # Reset password.
    r = client.post("/auth/reset-password", json={"token": reset_token, "new_password": "newpass123"})
    assert r.status_code == 200
    assert "thanh cong" in r.json()["message"]

    # Can't reuse reset token.
    r = client.post("/auth/reset-password", json={"token": reset_token, "new_password": "newpass456"})
    assert r.status_code == 400

    # Login with new password.
    r = client.post("/auth/login", json={"username": "kien", "password": "newpass123"})
    assert r.status_code == 200

    # Old password fails.
    r = client.post("/auth/login", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 401


def test_forgot_password_nonexistent_user(client):
    """Forgot password fails closed identically when SMTP is unavailable."""
    r = client.post("/auth/forgot-password", json={"username": "khong-ton-tai"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "EMAIL_SERVICE_UNAVAILABLE"
    assert "token" not in r.text.lower()


# ---- Tests: Refresh tokens ----

def test_refresh_token_rotation(client, user_repo):
    """Login -> refresh -> new access + refresh tokens -> old refresh invalid."""
    access = _register(client)

    # Create a refresh token manually in DB for this user.
    user = user_repo.get_by_username("kien")
    jti = "test-jti-1"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    user_repo.store_refresh_token(jti, user.id, expires_at)

    # Build refresh token JWT.
    from src.auth.security import create_refresh_token
    refresh_tok = create_refresh_token(user.id, jti)

    # Refresh.
    r = client.post("/auth/refresh", json={"refresh_token": refresh_tok})
    assert r.status_code == 200
    data = r.json()
    new_access = data["access_token"]
    new_refresh = data["refresh_token"]

    # New access token is valid.
    assert decode_token(new_access) == user.id

    # Old refresh token is revoked (decode old refresh token JWT, get jti, check DB).
    old_decoded = __import__("src.auth.security", fromlist=["decode_refresh_token"]).decode_refresh_token(refresh_tok)
    assert old_decoded is not None
    old_jti = old_decoded[1]
    assert user_repo.is_refresh_token_valid(old_jti) is None


def test_revoke_invalidates_token(client, user_repo):
    """Create refresh token -> revoke it -> refresh fails."""
    access = _register(client)
    user = user_repo.get_by_username("kien")
    jti = "test-jti-2"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    user_repo.store_refresh_token(jti, user.id, expires_at)

    from src.auth.security import create_refresh_token
    refresh_tok = create_refresh_token(user.id, jti)

    # Revoke.
    r = client.post("/auth/revoke", json={"refresh_token": refresh_tok})
    assert r.status_code == 200

    # Refresh should now fail.
    r = client.post("/auth/refresh", json={"refresh_token": refresh_tok})
    assert r.status_code == 401


# ---- Tests: MFA ----

def test_mfa_setup_and_verify(client, user_repo):
    """Setup MFA -> get secret -> verify with correct code -> MFA enabled."""
    token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Setup.
    r = client.post("/auth/mfa/setup", headers=headers)
    assert r.status_code == 200
    data = r.json()
    secret = data["secret"]
    backup_codes = data["backup_codes"]

    # Secret is base32, 32 chars.
    assert len(secret) == 32
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)

    # Backup codes: 8 codes, 6 digits each.
    assert len(backup_codes) == 8
    assert all(len(c) == 6 and c.isdigit() for c in backup_codes)

    # Generate a TOTP code for the secret.
    # We can't easily get the exact current code without the same hotp logic,
    # but verify_totp with window=1 should accept a freshly generated one.
    from src.auth.mfa import get_totp_token
    code = get_totp_token(secret)

    # Verify.
    r = client.post("/auth/mfa/verify", json={"code": code}, headers=headers)
    assert r.status_code == 200
    assert "da duoc bat" in r.json()["message"]

    # MFA enabled in DB.
    info = user_repo.get_mfa_secret(user_repo.get_by_username("kien").id)
    assert info["enabled"] is True


def test_mfa_verify_rejects_bad_code(client, user_repo):
    """Setup MFA -> verify with wrong code -> 400."""
    token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/auth/mfa/setup", headers=headers)

    r = client.post("/auth/mfa/verify", json={"code": "000000"}, headers=headers)
    assert r.status_code == 400
    assert "khong hop le" in r.json()["error"]["message"]


# ---- Tests: TOTP security primitives ----

def test_verify_totp_accepts_valid_codes():
    """verify_totp accepts a code within window."""
    from src.auth.security import create_totp_secret
    secret = create_totp_secret()
    from src.auth.mfa import get_totp_token
    code = get_totp_token(secret)
    assert verify_totp(secret, code) is True


def test_verify_totp_rejects_invalid_code():
    """verify_totp rejects wrong code."""
    from src.auth.security import create_totp_secret
    secret = create_totp_secret()
    assert verify_totp(secret, "000000") is False


def test_generate_backup_codes():
    """generate_backup_codes returns N unique 6-digit codes."""
    codes = generate_backup_codes(8)
    assert len(codes) == 8
    assert len(set(codes)) == 8
    for c in codes:
        assert len(c) == 6 and c.isdigit()


def test_hash_token_is_sha256():
    """hash_token returns a 64-char hex string."""
    h = hash_token("some-token")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_create_email_token_is_unique():
    """create_email_token returns unique values."""
    t1 = create_email_token()
    t2 = create_email_token()
    assert t1 != t2
    assert len(t1) > 20
