"""Google OAuth login and explicit account linking endpoints."""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..auth.security import create_refresh_token, create_token
from ..config import get_settings
from ..errors import AppError
from ..models import User
from ..store.sql_repository import UserRepository
from ..api.deps import current_user as current_user_local, user_repo as _get_repo

router = APIRouter(prefix="/auth/google", tags=["google-oauth"])
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_STATE_TTL = 600
_EXCHANGE_TTL = 60
_GOOGLE_ONLY_PASSWORD_HASH = "!" * 60
_states: dict[str, tuple[dict, float]] = {}
_exchanges: dict[str, tuple[str, float]] = {}


class ExchangeRequest(BaseModel):
    code: str


def _enabled() -> None:
    if not get_settings().google_oauth_enabled:
        raise AppError("OAUTH_UNAVAILABLE", "Google OAuth is not configured.", status=503)


def _state(action: str, user_id: str | None = None) -> str:
    settings = get_settings()
    nonce = secrets.token_urlsafe(24)
    payload = {
        "type": "google-oauth", "action": action, "nonce": nonce,
        "exp": datetime.now(UTC) + timedelta(seconds=_STATE_TTL),
    }
    if user_id:
        payload["sub"] = user_id
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    _states[nonce] = (payload, time.time() + _STATE_TTL)
    return token


def _decode_state(value: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(value, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "google-oauth" or payload.get("action") not in {"login", "link"}:
            raise ValueError
        nonce = payload.get("nonce")
        stored = _states.pop(nonce, None)
        if not nonce or not stored or stored[1] < time.time() or stored[0].get("action") != payload.get("action"):
            raise ValueError
        return payload
    except Exception as exc:
        raise AppError("INVALID_OAUTH_STATE", "OAuth state is invalid or expired.", status=400) from exc


def _auth_redirect(state: str) -> RedirectResponse:
    settings = get_settings()
    payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code", "scope": "openid email profile",
        "state": state, "nonce": payload["nonce"], "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(_AUTH_URL + "?" + urlencode(params), status_code=307)


@router.get("/status")
async def google_status():
    """Whether Google OAuth is fully configured (public — lets the login UI hide the button)."""
    return {"enabled": bool(get_settings().google_oauth_enabled)}


@router.get("/start")
async def google_start():
    _enabled()
    return _auth_redirect(_state("login"))


@router.get("/link")
@router.get("/link/start")
async def google_link_start(user: User = Depends(current_user_local)):
    _enabled()
    return _auth_redirect(_state("link", user.id))


async def _google_identity(code: str, state_nonce: str) -> tuple[str, str, str]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(_TOKEN_URL, data={
            "code": code, "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        })
        if response.status_code != 200:
            raise AppError("OAUTH_TOKEN_FAILED", "Google authorization failed.", status=400)
        id_token = response.json().get("id_token")
        if not id_token:
            raise AppError("OAUTH_TOKEN_FAILED", "Google did not return an identity token.", status=400)
        keys_response = await client.get(_JWKS_URL)
        keys_response.raise_for_status()
        header = jwt.get_unverified_header(id_token)
        key = next((item for item in keys_response.json().get("keys", []) if item.get("kid") == header.get("kid")), None)
        if not key:
            raise AppError("OAUTH_TOKEN_FAILED", "Google identity token key is unknown.", status=400)
        claims = jwt.decode(
            id_token, key, algorithms=["RS256"], audience=settings.google_oauth_client_id,
            issuer=["https://accounts.google.com", "accounts.google.com"],
        )
        if (
            claims.get("nonce") != state_nonce or not claims.get("sub")
            or not claims.get("email") or claims.get("email_verified") is not True
        ):
            raise AppError("OAUTH_TOKEN_FAILED", "Google identity token validation failed.", status=400)
        return claims["sub"], claims["email"].strip().casefold(), claims.get("name", "")


def _tokens(user: User, repo: UserRepository) -> dict:
    jti = uuid.uuid4().hex
    repo.store_refresh_token(jti, user.id, (datetime.now(UTC) + timedelta(days=30)).isoformat())
    return {"access_token": create_token(user.id), "refresh_token": create_refresh_token(user.id, jti), "token_type": "bearer"}


@router.get("/callback")
async def google_callback(code: str, state: str):
    _enabled()
    claims = _decode_state(state)
    provider_id, email, _name = await _google_identity(code, claims["nonce"])
    repo = _get_repo()
    existing = repo.get_oauth_account("google", provider_id)
    if claims["action"] == "link":
        user_id = claims.get("sub")
        if not user_id or not repo.get(user_id):
            raise AppError("INVALID_OAUTH_STATE", "OAuth state is invalid.", status=400)
        if existing and existing.user_id != user_id:
            raise AppError("OAUTH_ALREADY_LINKED", "Google account is already linked.", status=409)
        if not existing:
            try:
                repo.link_oauth_account(user_id, "google", provider_id, email)
            except Exception as exc:
                if repo.get_oauth_account("google", provider_id):
                    raise AppError("OAUTH_ALREADY_LINKED", "Google account is already linked.", status=409) from exc
                raise
        return RedirectResponse("/?google_link=success", status_code=303)

    if existing:
        user = repo.get(existing.user_id)
        if not user:
            raise AppError("OAUTH_ACCOUNT_INVALID", "Google account link is invalid.", status=409)
    else:
        if repo.get_by_email(email):
            return RedirectResponse("/?google_error=email_exists", status_code=303)
        username = email.split("@", 1)[0][:54] or "google-user"
        if repo.get_by_username(username):
            username = f"{username}-{secrets.token_hex(3)}"
        user = repo.create(username, _GOOGLE_ONLY_PASSWORD_HASH, email=email)
        repo.set_verified(user.id, True)
        repo.link_oauth_account(user.id, "google", provider_id, email)

    exchange_code = secrets.token_urlsafe(32)
    _exchanges[exchange_code] = (user.id, time.time() + _EXCHANGE_TTL)
    return RedirectResponse(f"/?google_code={exchange_code}", status_code=303)


@router.post("/exchange")
async def google_exchange(payload: ExchangeRequest):
    record = _exchanges.pop(payload.code, None)
    if not record or record[1] < time.time():
        raise AppError("INVALID_OAUTH_CODE", "Google sign-in code is invalid or expired.", status=400)
    repo = _get_repo()
    user = repo.get(record[0])
    if not user:
        raise AppError("INVALID_OAUTH_CODE", "Google sign-in code is invalid or expired.", status=400)
    return _tokens(user, repo)


@router.delete("/unlink")
@router.delete("/link")
async def google_unlink(user: User = Depends(current_user_local)):
    repo = _get_repo()
    accounts = repo.list_oauth_accounts(user.id)
    if not accounts:
        raise AppError("OAUTH_NOT_LINKED", "Google account is not linked.", status=404)
    if user.password_hash == _GOOGLE_ONLY_PASSWORD_HASH and len(accounts) <= 1:
        raise AppError("LAST_LOGIN_METHOD", "Cannot unlink the last login method.", status=400)
    if not repo.unlink_oauth_account(user.id, "google"):
        raise AppError("OAUTH_NOT_LINKED", "Google account is not linked.", status=404)
    return {"message": "Google account unlinked."}
