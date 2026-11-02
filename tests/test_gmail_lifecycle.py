from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import Request

from src.config import override_settings, reset_settings
from src.models import GmailConnection, User
from src.routers import gmail_oauth
from src.workers import gmail_worker
from src.workers import gmail_worker as gmail_worker_module


@pytest.fixture(autouse=True)
def gmail_settings(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("JWT_SECRET", "gmail-test-secret-12345678901234567890")
    override_settings(
        gmail_oauth_client_id="client-id",
        gmail_oauth_client_secret="client-secret",
        gmail_oauth_redirect_uri="https://app.test/auth/gmail/callback",
        gmail_token_encryption_key=key,
        gmail_pubsub_topic="projects/test/topics/gmail",
        gmail_payment_sender_filters=["bank.example"],
        gmail_payment_subject_filters=["paid"],
    )
    gmail_oauth._states.clear()
    gmail_oauth._codes.clear()
    yield
    reset_settings()


@pytest.mark.asyncio
async def test_gmail_oauth_start_and_state_is_single_use():
    response = await gmail_oauth.gmail_start(
        User(id="user-1", username="u", password_hash="x" * 60)
    )
    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    state = query["state"][0]
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://app.test/auth/gmail/callback"]
    assert query["scope"] == ["openid email profile https://www.googleapis.com/auth/gmail.metadata"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["nonce"]
    assert gmail_oauth._decode_state(state)["sub"] == "user-1"
    with pytest.raises(Exception) as exc_info:
        gmail_oauth._decode_state(state)
    assert exc_info.value.code == "INVALID_OAUTH_STATE"


@pytest.mark.asyncio
async def test_gmail_oauth_callback_stores_encrypted_refresh_and_starts_watch(monkeypatch):
    connection = GmailConnection(id="connection-1", user_id="user-1", gmail_account_id="google-1")
    repo = Mock()
    repo.create_connection.return_value = connection
    start_watch = AsyncMock()
    monkeypatch.setattr(gmail_oauth, "_repo", lambda: repo)
    monkeypatch.setattr(
        gmail_oauth,
        "_exchange",
        AsyncMock(return_value=({"sub": "google-1", "email": "person@gmail.com"}, "refresh-token")),
    )
    monkeypatch.setattr(
        gmail_oauth, "_fernet", lambda: Mock(encrypt=Mock(return_value=b"encrypted"))
    )
    monkeypatch.setattr(gmail_worker_module, "start_gmail_watch", start_watch)

    state = gmail_oauth._new_state("user-1")
    response = await gmail_oauth.gmail_callback("auth-code", state)

    assert response.status_code == 303
    assert "gmail_status=success" in response.headers["location"]
    assert "refresh-token" not in response.headers["location"]
    callback_query = parse_qs(urlparse(response.headers["location"]).query)
    assert callback_query["gmail_code"][0]
    assert len(callback_query["gmail_code"][0]) >= 20
    repo.create_connection.assert_called_once_with(
        "user-1", "google-1", email="person@gmail.com", encrypted_refresh_token="encrypted"
    )
    start_watch.assert_awaited_once_with("connection-1")


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "failed", request=Mock(), response=Mock(status_code=self.status_code)
            )


class _HttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        response = self.responses[url]
        return response() if callable(response) else response

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        response = self.responses[url]
        return response() if callable(response) else response


@pytest.mark.asyncio
async def test_worker_access_token_and_watch_send_expected_google_requests(monkeypatch):
    client = _HttpClient(
        {
            gmail_worker._TOKEN_URL: _Response(payload={"access_token": "access-1"}),
            f"{gmail_worker._GMAIL_API}/watch": _Response(
                payload={"historyId": "h-1", "expiration": "1735689600000"}
            ),
        }
    )
    monkeypatch.setattr(gmail_worker.httpx, "AsyncClient", lambda **kwargs: client)

    token = await gmail_worker._access_token("refresh-1")
    history_id, expiration = await gmail_worker._watch(token)

    assert token == "access-1"
    assert history_id == "h-1"
    assert expiration == datetime.fromtimestamp(1735689600, UTC)
    assert client.calls[0][2]["data"]["refresh_token"] == "refresh-1"
    assert client.calls[1][2]["json"]["topicName"] == "projects/test/topics/gmail"


@pytest.mark.asyncio
async def test_worker_invalid_grant_raises_dedicated_error(monkeypatch):
    monkeypatch.setattr(
        gmail_worker.httpx,
        "AsyncClient",
        lambda **kwargs: _HttpClient(
            {gmail_worker._TOKEN_URL: _Response(400, {"error": "invalid_grant"})}
        ),
    )
    with pytest.raises(gmail_worker.InvalidGrantError):
        await gmail_worker._access_token("revoked-refresh")


@pytest.mark.asyncio
async def test_worker_start_watch_clears_invalid_grant_and_persists_watch(monkeypatch):
    connection = GmailConnection(
        id="c-1", user_id="u-1", gmail_account_id="g-1", encrypted_refresh_token="cipher"
    )
    repo = Mock()
    repo.get_connection_unscoped.return_value = connection
    monkeypatch.setattr(gmail_worker, "_repo", lambda: repo)
    monkeypatch.setattr(gmail_worker, "_refresh_token", lambda c: "refresh")
    monkeypatch.setattr(gmail_worker, "_access_token", AsyncMock(return_value="access"))
    expiration = datetime.now(UTC) + timedelta(hours=24)
    monkeypatch.setattr(gmail_worker, "_watch", AsyncMock(return_value=("h-2", expiration)))

    await gmail_worker.start_gmail_watch("c-1")
    repo.update_watch.assert_called_once_with(
        "c-1",
        "u-1",
        history_id="h-2",
        watch_expiration=expiration,
        watch_topic="projects/test/topics/gmail",
    )

    gmail_worker._access_token.side_effect = gmail_worker.InvalidGrantError
    await gmail_worker.start_gmail_watch("c-1")
    repo.clear_credentials.assert_called_once_with("c-1")


@pytest.mark.asyncio
async def test_worker_renewal_and_stop_revoke(monkeypatch):
    expiring = GmailConnection(
        id="c-1",
        user_id="u-1",
        gmail_account_id="g-1",
        encrypted_refresh_token="cipher",
        watch_expiration=datetime.now(UTC) + timedelta(minutes=5),
    )
    repo = Mock()
    repo.list_all_connections.return_value = [expiring]
    monkeypatch.setattr(gmail_worker, "_repo", lambda: repo)
    start = AsyncMock()
    monkeypatch.setattr(gmail_worker, "start_gmail_watch", start)
    assert await gmail_worker._renew_expiring_watches() == 1
    start.assert_awaited_once_with("c-1")

    client = _HttpClient(
        {
            gmail_worker._TOKEN_URL: _Response(payload={"access_token": "access"}),
            f"{gmail_worker._GMAIL_API}/stop": _Response(),
            gmail_worker._REVOKE_URL: _Response(),
        }
    )
    monkeypatch.setattr(gmail_worker.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(gmail_worker, "_refresh_token", lambda c: "refresh")
    await gmail_worker.stop_and_revoke_gmail(expiring)
    assert [call[0:2] for call in client.calls] == [
        ("post", gmail_worker._TOKEN_URL),
        ("post", f"{gmail_worker._GMAIL_API}/stop"),
        ("post", gmail_worker._REVOKE_URL),
    ]
    assert client.calls[-1][2]["params"] == {"token": "refresh"}


@pytest.mark.asyncio
async def test_worker_sync_fetches_metadata_only_deduplicates_and_renews(monkeypatch):
    connection = GmailConnection(
        id="c-1",
        user_id="u-1",
        gmail_account_id="g-1",
        encrypted_refresh_token="cipher",
        history_id="old",
        watch_expiration=datetime.now(UTC) + timedelta(minutes=5),
    )
    repo = Mock()
    repo.get_connection_unscoped.return_value = connection
    repo.get_payment_alert_by_message.return_value = None
    client = _HttpClient(
        {
            f"{gmail_worker._GMAIL_API}/history": _Response(
                payload={
                    "historyId": "new",
                    "history": [{"messagesAdded": [{"id": "m-1"}, {"id": "m-1"}]}],
                }
            ),
            f"{gmail_worker._GMAIL_API}/messages/m-1": _Response(
                payload={
                    "threadId": "t-1",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "alerts@bank.example"},
                            {"name": "Subject", "value": "Payment paid"},
                            {"name": "Date", "value": "today"},
                        ]
                    },
                }
            ),
        }
    )
    monkeypatch.setattr(gmail_worker, "_repo", lambda: repo)
    monkeypatch.setattr(gmail_worker, "_refresh_token", lambda c: "refresh")
    monkeypatch.setattr(gmail_worker, "_access_token", AsyncMock(return_value="access"))
    monkeypatch.setattr(gmail_worker.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(
        gmail_worker,
        "_watch",
        AsyncMock(return_value=("renewed", datetime.now(UTC) + timedelta(days=1))),
    )
    monkeypatch.setattr("src.push.send_push_to_user", Mock())

    assert await gmail_worker._sync("c-1") == 1
    repo.upsert_payment_alert.assert_called_once()
    repo.update_cursor.assert_called_once_with("c-1", "u-1", "new")
    assert repo.update_watch.call_count == 1
    message_call = next(call for call in client.calls if call[1].endswith("/messages/m-1"))
    assert message_call[2]["params"]["format"] == "metadata"
    assert "metadataHeaders" in message_call[2]["params"]


async def _request(body, headers=None) -> Request:
    raw = json.dumps(body).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/gmail/pubsub",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_pubsub_authenticates_verification_token_and_dispatches(monkeypatch):
    payload = base64.b64encode(
        json.dumps({"emailAddress": "person@gmail.com", "historyId": "42"}).encode()
    ).decode()
    connection = GmailConnection(
        id="c-1", user_id="u-1", gmail_account_id="g-1", email="person@gmail.com"
    )
    repo = Mock()
    repo.list_connections_by_email.return_value = [connection]
    task = Mock()
    monkeypatch.setattr(gmail_oauth, "_repo", lambda: repo)
    monkeypatch.setattr(gmail_oauth, "_verify_pubsub_oidc", AsyncMock())
    monkeypatch.setattr(gmail_worker_module, "sync_gmail_connection", SimpleNamespace(delay=task))
    override_settings(gmail_pubsub_verification_token="verify-me", gmail_pubsub_audience="audience")

    response = await gmail_oauth.gmail_pubsub(
        await _request(
            {"message": {"data": payload}},
            {"x-verification-token": "verify-me", "authorization": "Bearer token"},
        )
    )

    assert response == {"ok": True}
    gmail_oauth._verify_pubsub_oidc.assert_awaited_once()
    task.assert_called_once_with("c-1")


@pytest.mark.asyncio
async def test_pubsub_rejects_missing_auth_and_bad_payload():
    override_settings(gmail_pubsub_verification_token="verify-me", gmail_pubsub_audience="")
    with pytest.raises(Exception) as exc_info:
        await gmail_oauth.gmail_pubsub(await _request({}, {}))
    assert exc_info.value.code == "FORBIDDEN"

    with pytest.raises(Exception) as exc_info:
        await gmail_oauth.gmail_pubsub(
            await _request(
                {"message": {"data": "not-base64"}}, {"x-verification-token": "verify-me"}
            )
        )
    assert exc_info.value.code == "INVALID_PUBSUB"
