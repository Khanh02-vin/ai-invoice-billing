"""Gmail metadata-only synchronization and watch lifecycle tasks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import Fernet

from ..config import get_settings
from ..store.sql_repository import GmailRepository, get_database
from .celery_app import app

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_WATCH_RENEWAL_WINDOW = timedelta(hours=1)


class InvalidGrantError(Exception):
    """Google has permanently invalidated the stored refresh grant."""


def _repo():
    try:
        from .. import app as app_module
        db = getattr(getattr(app_module, "users", None), "db", None)
        if db is not None:
            return GmailRepository(db)
    except Exception:
        pass
    return GmailRepository(get_database(get_settings().database_path))


def _refresh_token(connection) -> str:
    return Fernet(get_settings().gmail_token_encryption_key.encode()).decrypt(
        connection.encrypted_refresh_token.encode()
    ).decode()


async def _access_token(refresh_token: str) -> str:
    s = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(_TOKEN_URL, data={
            "client_id": s.gmail_oauth_client_id, "client_secret": s.gmail_oauth_client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        })
        if response.status_code == 400:
            try:
                if response.json().get("error") == "invalid_grant":
                    raise InvalidGrantError
            except ValueError:
                pass
        response.raise_for_status()
        return response.json()["access_token"]


async def _watch(access_token: str) -> tuple[str | None, datetime | None]:
    topic = get_settings().gmail_pubsub_topic
    if not topic:
        raise RuntimeError("GMAIL_PUBSUB_TOPIC is not configured")
    async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {access_token}"}) as client:
        response = await client.post(f"{_GMAIL_API}/watch", json={
            "topicName": topic,
            "labelIds": ["INBOX"],
            "labelFilterAction": "include",
        })
        response.raise_for_status()
        payload = response.json()
    expiration = payload.get("expiration")
    return payload.get("historyId"), (
        datetime.fromtimestamp(int(expiration) / 1000, UTC) if expiration else None
    )


async def start_gmail_watch(connection_id: str) -> None:
    """Create the initial Gmail watch and persist its baseline cursor."""
    repo = _repo()
    connection = repo.get_connection_unscoped(connection_id)
    if not connection or not connection.encrypted_refresh_token:
        return
    try:
        token = await _access_token(_refresh_token(connection))
        history_id, expiration = await _watch(token)
    except InvalidGrantError:
        repo.clear_credentials(connection.id)
        return
    repo.update_watch(connection.id, connection.user_id, history_id=history_id,
                      watch_expiration=expiration, watch_topic=get_settings().gmail_pubsub_topic)


async def stop_and_revoke_gmail(connection) -> None:
    """Best-effort stop/revoke before local connection deletion."""
    if not connection.encrypted_refresh_token:
        return
    try:
        refresh = _refresh_token(connection)
        try:
            token = await _access_token(refresh)
            async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {token}"}) as client:
                await client.post(f"{_GMAIL_API}/stop")
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(_REVOKE_URL, params={"token": refresh})
        except Exception:
            pass
    except Exception:
        # Local disconnect must still succeed if Google is unavailable.
        return


async def _sync(connection_id: str) -> int:
    repo = _repo()
    connection = repo.get_connection_unscoped(connection_id)
    if not connection or not connection.encrypted_refresh_token:
        return 0
    try:
        token = await _access_token(_refresh_token(connection))
    except InvalidGrantError:
        repo.clear_credentials(connection.id)
        return 0
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        params = {"userId": "me", "maxResults": 100}
        if connection.history_id:
            params["startHistoryId"] = connection.history_id
        response = await client.get(f"{_GMAIL_API}/history", params=params)
        if response.status_code == 404:
            params.pop("startHistoryId", None)
            response = await client.get(f"{_GMAIL_API}/history", params=params)
        response.raise_for_status()
        payload = response.json()
        message_ids = []
        for item in payload.get("history", []):
            message_ids.extend(m.get("id") for m in item.get("messagesAdded", []) if m.get("id"))
        count = 0
        for message_id in dict.fromkeys(message_ids):
            message = await client.get(
                f"{_GMAIL_API}/messages/{message_id}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date", "Message-ID"]},
            )
            if message.status_code != 200:
                continue
            data = message.json()
            headers_map = {h.get("name", "").lower(): h.get("value", "")
                           for h in data.get("payload", {}).get("headers", [])}
            sender = headers_map.get("from", "")
            subject = headers_map.get("subject", "")
            sender_match = any(f.casefold() in sender.casefold() for f in get_settings().gmail_payment_sender_filters)
            subject_match = any(f.casefold() in subject.casefold() for f in get_settings().gmail_payment_subject_filters)
            if get_settings().gmail_payment_sender_filters and not sender_match:
                continue
            if not subject_match:
                continue
            existing = repo.get_payment_alert_by_message(connection.id, message_id, connection.user_id)
            repo.upsert_payment_alert(connection.user_id, connection.id, message_id,
                                      thread_id=data.get("threadId", ""), sender=sender, subject=subject,
                                      raw_payload={"date": headers_map.get("date", "")})
            if existing is None:
                try:
                    from ..push import send_push_to_user
                    send_push_to_user(
                        connection.user_id,
                        "Có email thanh toán mới",
                        "Kiểm tra email và nhập số tiền đã chuyển để đối chiếu.",
                        url="/",
                    )
                except Exception:
                    pass
            count += 1
        latest = payload.get("historyId") or connection.history_id
        if latest:
            repo.update_cursor(connection.id, connection.user_id, str(latest))

    # Renew after processing the old cursor so renewal does not skip history.
    if not connection.watch_expiration or connection.watch_expiration <= datetime.now(UTC) + _WATCH_RENEWAL_WINDOW:
        try:
            history_id, expiration = await _watch(token)
            repo.update_watch(connection.id, connection.user_id, history_id=history_id,
                              watch_expiration=expiration, watch_topic=get_settings().gmail_pubsub_topic)
        except Exception:
            pass
    return count


@app.task(bind=True, name="src.workers.gmail_worker.sync_gmail_connection")
def sync_gmail_connection(self, connection_id: str):
    import asyncio
    try:
        return asyncio.run(_sync(connection_id))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60, max_retries=3)


async def _renew_expiring_watches() -> int:
    repo = _repo()
    renewed = 0
    threshold = datetime.now(UTC) + _WATCH_RENEWAL_WINDOW
    for connection in repo.list_all_connections():
        if not connection.encrypted_refresh_token:
            continue
        if connection.watch_expiration and connection.watch_expiration > threshold:
            continue
        try:
            await start_gmail_watch(connection.id)
        except Exception:
            # One transient account failure must not block renewal for others.
            continue
        renewed += 1
    return renewed


@app.task(bind=True, name="src.workers.gmail_worker.renew_gmail_watches")
def renew_gmail_watches(self):
    import asyncio
    try:
        return asyncio.run(_renew_expiring_watches())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)
