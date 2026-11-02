"""Separate Gmail metadata OAuth and Pub/Sub endpoints."""
from __future__ import annotations

import base64
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..errors import AppError
from ..models import GmailConnection, PaymentTransaction, TxDirection, User
from ..store.sql_repository import GmailRepository, TransactionRepository, InvoiceRepository, get_database
from .auth_ext import current_user_local

router = APIRouter(prefix="/auth/gmail", tags=["gmail"])
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
_STATE_TTL = 600
_states: dict[str, tuple[dict, float]] = {}
_codes: dict[str, tuple[str, float]] = {}


def _db():
    try:
        from .. import app as app_module
        db = getattr(getattr(app_module, "users", None), "db", None)
        if db is not None:
            return db
    except Exception:
        pass
    return get_database(get_settings().database_path)


def _repo() -> GmailRepository:
    return GmailRepository(_db())


def _enabled() -> None:
    if not get_settings().gmail_oauth_enabled:
        raise AppError("GMAIL_UNAVAILABLE", "Gmail OAuth is not configured.", status=503)


def _fernet() -> Fernet:
    try:
        return Fernet(get_settings().gmail_token_encryption_key.encode())
    except (ValueError, TypeError):
        raise AppError("GMAIL_UNAVAILABLE", "Gmail token encryption is not configured.", status=503)


def _new_state(user_id: str) -> str:
    nonce = secrets.token_urlsafe(24)
    payload = {"type": "gmail-oauth", "action": "connect", "sub": user_id, "nonce": nonce,
               "exp": datetime.now(UTC) + timedelta(seconds=_STATE_TTL)}
    token = jwt.encode(payload, get_settings().jwt_secret, algorithm=get_settings().jwt_algorithm)
    _states[nonce] = (payload, time.time() + _STATE_TTL)
    return token


def _decode_state(value: str) -> dict:
    try:
        payload = jwt.decode(value, get_settings().jwt_secret,
                             algorithms=[get_settings().jwt_algorithm])
        nonce = payload.get("nonce")
        stored = _states.pop(nonce, None)
        if payload.get("type") != "gmail-oauth" or payload.get("action") != "connect" \
                or not stored or stored[1] < time.time() or stored[0].get("sub") != payload.get("sub"):
            raise ValueError
        return payload
    except Exception as exc:
        raise AppError("INVALID_OAUTH_STATE", "Gmail OAuth state is invalid or expired.", status=400) from exc


@router.get("/start")
async def gmail_start(user: User = Depends(current_user_local)):
    _enabled()
    state = _new_state(user.id)
    payload = jwt.decode(state, get_settings().jwt_secret, algorithms=[get_settings().jwt_algorithm])
    params = {
        "client_id": get_settings().gmail_oauth_client_id,
        "redirect_uri": get_settings().gmail_oauth_redirect_uri,
        "response_type": "code",
        "scope": f"openid email profile {_GMAIL_SCOPE}",
        "access_type": "offline", "prompt": "consent",
        "state": state, "nonce": payload["nonce"],
    }
    return RedirectResponse(_AUTH_URL + "?" + urlencode(params), status_code=307)


async def _exchange(code: str, nonce: str) -> tuple[dict, str]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(_TOKEN_URL, data={
            "code": code, "client_id": s.gmail_oauth_client_id,
            "client_secret": s.gmail_oauth_client_secret,
            "redirect_uri": s.gmail_oauth_redirect_uri,
            "grant_type": "authorization_code",
        })
        if response.status_code != 200:
            raise AppError("OAUTH_TOKEN_FAILED", "Gmail authorization failed.", status=400)
        data = response.json()
        refresh = data.get("refresh_token")
        if not refresh:
            raise AppError("GMAIL_REFRESH_TOKEN_MISSING", "Google did not return a refresh token.", status=400)
        scopes = set((data.get("scope") or "").split())
        if _GMAIL_SCOPE not in scopes:
            raise AppError("GMAIL_SCOPE_MISSING", "Gmail metadata permission was not granted.", status=400)
        id_token = data.get("id_token")
        if not id_token:
            raise AppError("OAUTH_TOKEN_FAILED", "Google did not return an identity token.", status=400)
        keys = (await client.get(_JWKS_URL)).json().get("keys", [])
        header = jwt.get_unverified_header(id_token)
        key = next((item for item in keys if item.get("kid") == header.get("kid")), None)
        if not key:
            raise AppError("OAUTH_TOKEN_FAILED", "Google identity key is unknown.", status=400)
        claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=s.gmail_oauth_client_id,
                           issuer="https://accounts.google.com")
        if claims.get("nonce") != nonce or not claims.get("sub") or not claims.get("email") \
                or claims.get("email_verified") is not True:
            raise AppError("OAUTH_TOKEN_FAILED", "Google identity validation failed.", status=400)
        return claims, refresh


@router.get("/callback")
async def gmail_callback(code: str, state: str):
    _enabled()
    claims = _decode_state(state)
    identity, refresh = await _exchange(code, claims["nonce"])
    connection = _repo().create_connection(
        claims["sub"], identity["sub"], email=identity["email"],
        encrypted_refresh_token=_fernet().encrypt(refresh.encode()).decode(),
    )
    try:
        from ..workers.gmail_worker import start_gmail_watch
        await start_gmail_watch(connection.id)
    except Exception as exc:
        raise AppError("GMAIL_WATCH_FAILED", "Gmail watch could not be created.", status=502) from exc
    _codes[(opaque := secrets.token_urlsafe(24))] = (connection.user_id, time.time() + 60)
    return RedirectResponse(f"/?gmail_status=success&gmail_code={opaque}", status_code=303)


@router.get("/status")
async def gmail_status(user: User = Depends(current_user_local)):
    connections = _repo().list_connections(user.id)
    if not connections:
        return {"connected": False, "email": None, "watch_expiration": None}
    c = connections[0]
    connected = bool(c.encrypted_refresh_token)
    return {"connected": connected, "email": c.email if connected else None,
            "watch_expiration": c.watch_expiration if connected else None,
            "history_id": c.history_id if connected else None}


@router.delete("")
async def gmail_disconnect(user: User = Depends(current_user_local)):
    repo = _repo()
    connections = repo.list_connections(user.id)
    for c in connections:
        try:
            from ..workers.gmail_worker import stop_and_revoke_gmail
            await stop_and_revoke_gmail(c)
        finally:
            repo.disconnect(c.id, user.id)
    return {"message": "Gmail disconnected."}


@router.get("/alerts")
async def gmail_alerts(user: User = Depends(current_user_local)):
    repo = _repo()
    return [a.model_dump(mode="json") for c in repo.list_connections(user.id)
            for a in repo.list_payment_alerts(c.id, user.id)]


class AlertAmountRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(default="VND", min_length=3, max_length=3)


@router.post("/alerts/{alert_id}/transaction")
async def gmail_alert_transaction(alert_id: str, body: AlertAmountRequest,
                                  user: User = Depends(current_user_local)):
    alert = _repo().get_payment_alert(alert_id, user.id)
    if not alert:
        raise AppError("NOT_FOUND", "Payment alert not found.", status=404)
    tx_repo = TransactionRepository(_db())
    tx = tx_repo.create(PaymentTransaction(
        user_id=user.id, amount=body.amount, currency=body.currency.upper(),
        direction=TxDirection.DEBIT, merchant=alert.sender, note=f"Gmail: {alert.subject}",
        external_ref=f"gmail:{alert.gmail_message_id}",
    ))
    match_status = "unmatched"
    from ..payments.matching import pick_invoices_decision
    from ..payments.service import link, link_many
    invoices = InvoiceRepository(_db()).list(user_id=user.id, limit=200)
    decision = pick_invoices_decision(tx, invoices)
    if decision.kind == "unique":
        if len(decision.invoices) > 1:
            link_many(tx_repo, InvoiceRepository(_db()), tx, decision.invoices)
        else:
            link(tx_repo, InvoiceRepository(_db()), tx, decision.invoices[0])
        match_status = "matched"
    elif decision.kind == "ambiguous":
        match_status = "ambiguous"
    return {"transaction": tx.model_dump(mode="json"), "payment_match_status": match_status}


async def _verify_pubsub_oidc(request: Request) -> None:
    settings = get_settings()
    token = request.headers.get("authorization", "")
    if not token.startswith("Bearer "):
        raise AppError("FORBIDDEN", "Missing Pub/Sub OIDC token.", status=403)
    try:
        raw = token[7:].strip()
        header = jwt.get_unverified_header(raw)
        async with httpx.AsyncClient(timeout=10) as client:
            keys = (await client.get(_JWKS_URL)).json().get("keys", [])
        key = next((item for item in keys if item.get("kid") == header.get("kid")), None)
        if not key:
            raise ValueError("unknown key")
        jwt.decode(raw, key, algorithms=["RS256"], audience=settings.gmail_pubsub_audience,
                   issuer=["https://accounts.google.com", "accounts.google.com"])
    except Exception as exc:
        raise AppError("FORBIDDEN", "Invalid Pub/Sub OIDC token.", status=403) from exc


@router.post("/webhook")
@router.post("/pubsub")
async def gmail_pubsub(request: Request):
    settings = get_settings()
    configured_token = settings.gmail_pubsub_verification_token
    if not configured_token and not settings.gmail_pubsub_audience:
        raise AppError("GMAIL_UNAVAILABLE", "Gmail Pub/Sub authentication is not configured.", status=503)
    if configured_token and request.headers.get("x-verification-token") != configured_token:
        raise AppError("FORBIDDEN", "Invalid Pub/Sub verification token.", status=403)
    if settings.gmail_pubsub_audience:
        await _verify_pubsub_oidc(request)
    try:
        body = await request.json()
        message = body.get("message", body)
        encoded = message.get("data")
        if not encoded:
            raise ValueError("missing data")
        data = json.loads(base64.b64decode(encoded).decode())
        email = data.get("emailAddress")
        history_id = str(data.get("historyId", ""))
    except Exception as exc:
        raise AppError("INVALID_PUBSUB", "Invalid Gmail Pub/Sub payload.", status=400) from exc
    if not email or not history_id:
        raise AppError("INVALID_PUBSUB", "Gmail Pub/Sub payload is incomplete.", status=400)
    connection = next(iter(_repo().list_connections_by_email(email)), None)
    if connection:
        try:
            from ..workers.gmail_worker import sync_gmail_connection
            sync_gmail_connection.delay(connection.id)
        except Exception:
            # Pub/Sub should acknowledge a valid notification even if the queue is unavailable.
            pass
    return {"ok": True}
