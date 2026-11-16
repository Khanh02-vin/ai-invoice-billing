//! Google OAuth login and explicit account linking endpoints — mirror
//! src/routers/google_oauth.py.
//!
//! State = JWT HS256 ({type:"google-oauth", action, nonce, exp, sub?}) + nonce
//! lưu in-memory single-use (TTL 600s). Exchange code → user_id in-memory (TTL
//! 60s). Identity từ Google: token exchange + JWKS RS256 (audience/issuer/nonce
//! validate qua jsonwebtoken).

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Redirect, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::error::{AppError, AppResult, RequestId};
use crate::extract::CurrentUser;
use crate::routes::AppState;
use crate::security::{create_refresh_token, create_token, random_hex6, uuid_hex32};
use crate::store;
use crate::timefmt;
use crate::validation::{body_object, finish, parse_body, req_str_field};

const AUTH_URL: &str = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_URL: &str = "https://oauth2.googleapis.com/token";
const JWKS_URL: &str = "https://www.googleapis.com/oauth2/v3/certs";
const STATE_TTL: f64 = 600.0;
const EXCHANGE_TTL: f64 = 60.0;
const GOOGLE_ONLY_PASSWORD_HASH: &str = "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!";

pub(crate) fn now_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn internal_err(rid: &RequestId, err: impl std::fmt::Debug) -> AppError {
    eprintln!("[quanh-ta] oauth_error request_id={} {err:?}", rid.0);
    AppError::new(
        "INTERNAL_ERROR",
        "Có lỗi xảy ra. Vui lòng thử lại.",
        StatusCode::INTERNAL_SERVER_ERROR,
    )
    .with_request_id(rid)
}

fn oauth_unavailable(rid: &RequestId) -> AppError {
    AppError::new(
        "OAUTH_UNAVAILABLE",
        "Google OAuth is not configured.",
        StatusCode::SERVICE_UNAVAILABLE,
    )
    .with_request_id(rid)
}

fn check_enabled(state: &AppState, rid: &RequestId) -> Result<(), AppError> {
    if !state.settings.google_oauth_enabled() {
        return Err(oauth_unavailable(rid));
    }
    Ok(())
}

// ---- State / exchange stores ----

#[derive(Serialize, Deserialize)]
struct StateClaims {
    #[serde(rename = "type")]
    token_type: String,
    action: String,
    nonce: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    sub: Option<String>,
    exp: u64,
}

/// _state của Python: JWT HS256 + nonce lưu in-memory single-use.
fn make_state(state: &AppState, action: &str, user_id: Option<&str>) -> String {
    let nonce = crate::security::new_jti(); // token_urlsafe(24) như Python
    let exp = (chrono::Utc::now() + chrono::Duration::seconds(STATE_TTL as i64)).timestamp() as u64;
    let claims = StateClaims {
        token_type: "google-oauth".into(),
        action: action.to_owned(),
        nonce: nonce.clone(),
        sub: user_id.map(str::to_owned),
        exp,
    };
    let token = crate::security::encode_hs256(&claims, &state.settings.jwt_secret);
    let mut states = state.oauth_states.lock().expect("oauth states lock");
    states.insert(nonce, (action.to_owned(), now_f64() + STATE_TTL));
    token
}

/// _decode_state của Python: decode + validate + POP nonce (single-use).
fn decode_state(state: &AppState, rid: &RequestId, value: &str) -> Result<StateClaims, AppError> {
    let invalid = || {
        AppError::new(
            "INVALID_OAUTH_STATE",
            "OAuth state is invalid or expired.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(rid)
    };
    let claims: StateClaims = crate::security::decode_hs256(value, &state.settings.jwt_secret)
        .ok_or_else(invalid)?;
    if claims.token_type != "google-oauth"
        || !matches!(claims.action.as_str(), "login" | "link")
    {
        return Err(invalid());
    }
    let mut states = state.oauth_states.lock().expect("oauth states lock");
    let stored = states.remove(&claims.nonce);
    match stored {
        Some((action, expiry)) if expiry >= now_f64() && action == claims.action => Ok(claims),
        _ => Err(invalid()),
    }
}

/// _auth_redirect của Python — 307 sang Google với query params.
fn auth_redirect(state: &AppState, rid: &RequestId, state_token: &str, nonce: &str) -> Result<Response, AppError> {
    let settings = &state.settings;
    let params = [
        ("client_id", settings.google_oauth_client_id.as_str()),
        ("redirect_uri", settings.google_oauth_redirect_uri.as_str()),
        ("response_type", "code"),
        ("scope", "openid email profile"),
        ("state", state_token),
        ("nonce", nonce),
        ("access_type", "online"),
        ("prompt", "select_account"),
    ];
    let query = form_urlencoded(&params);
    if query.is_none() {
        return Err(internal_err(rid, "oauth redirect encode failed"));
    }
    Ok(Redirect::temporary(&format!("{AUTH_URL}?{}", query.unwrap())).into_response())
}

/// urlencode của Python (form-encoding, space → '+').
fn form_urlencoded(params: &[(&str, &str)]) -> Option<String> {
    let mut out = String::new();
    for (key, value) in params {
        if !out.is_empty() {
            out.push('&');
        }
        out.push_str(&encode_component(key)?);
        out.push('=');
        out.push_str(&encode_component(value)?);
    }
    Some(out)
}

/// quote_via=quote_plus mặc định của urlencode: space → '+', ký tự an toàn
/// giữ nguyên, phần còn lại percent-encode UTF-8.
fn encode_component(s: &str) -> Option<String> {
    let mut out = String::new();
    for byte in s.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'.' | b'-' | b'*' | b'_' => {
                out.push(*byte as char)
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    Some(out)
}

// ---- Google identity ----

#[derive(Debug, Deserialize)]
struct TokenResponse {
    id_token: Option<String>,
}

#[derive(Debug, Deserialize)]
struct GoogleIdClaims {
    nonce: Option<String>,
    sub: Option<String>,
    email: Option<String>,
    email_verified: Option<bool>,
    #[serde(default)]
    name: Option<String>,
}

/// _google_identity: token exchange + JWKS RS256 + validate claims.
async fn google_identity(
    state: &AppState,
    rid: &RequestId,
    code: &str,
    state_nonce: &str,
) -> Result<(String, String, String), AppError> {
    let settings = &state.settings;
    let token_form = form_urlencoded(&[
        ("code", code),
        ("client_id", &settings.google_oauth_client_id),
        ("client_secret", &settings.google_oauth_client_secret),
        ("redirect_uri", &settings.google_oauth_redirect_uri),
        ("grant_type", "authorization_code"),
    ])
    .ok_or_else(|| internal_err(rid, "token form encode failed"))?;

    let token_resp = state
        .http
        .post(TOKEN_URL)
        .header("content-type", "application/x-www-form-urlencoded")
        .body(token_form)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| internal_err(rid, e))?;
    if token_resp.status() != StatusCode::OK {
        return Err(AppError::new(
            "OAUTH_TOKEN_FAILED",
            "Google authorization failed.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(rid));
    }
    let token_json: TokenResponse = token_resp
        .json()
        .await
        .map_err(|e| internal_err(rid, e))?;
    let Some(id_token) = token_json.id_token else {
        return Err(AppError::new(
            "OAUTH_TOKEN_FAILED",
            "Google did not return an identity token.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(rid));
    };

    let keys_resp = state
        .http
        .get(JWKS_URL)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| internal_err(rid, e))?;
    if !keys_resp.status().is_success() {
        return Err(internal_err(rid, format!("jwks status {}", keys_resp.status())));
    }
    let jwks: jsonwebtoken::jwk::JwkSet = keys_resp.json().await.map_err(|e| internal_err(rid, e))?;
    let header = crate::security::jwt_header(&id_token).ok_or_else(|| {
        AppError::new(
            "OAUTH_TOKEN_FAILED",
            "Google identity token key is unknown.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(rid)
    })?;
    let Some(key) = jwks
        .keys
        .iter()
        .find(|k| k.common.key_id.as_deref() == Some(header.kid.as_deref().unwrap_or("")))
    else {
        return Err(AppError::new(
            "OAUTH_TOKEN_FAILED",
            "Google identity token key is unknown.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(rid));
    };

    let claims_value = crate::security::decode_rs256_google(
        &id_token,
        key,
        &settings.google_oauth_client_id,
    )
    .ok_or_else(|| internal_err(rid, "google id_token decode failed"))?;
    let claims: GoogleIdClaims =
        serde_json::from_value(claims_value).map_err(|e| internal_err(rid, e))?;
    if claims.nonce.as_deref() != Some(state_nonce)
        || claims.sub.is_none()
        || claims.email.is_none()
        || claims.email_verified != Some(true)
    {
        return Err(AppError::new(
            "OAUTH_TOKEN_FAILED",
            "Google identity token validation failed.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(rid));
    }
    let email = claims.email.unwrap_or_default().trim().to_lowercase();
    Ok((claims.sub.unwrap_or_default(), email, claims.name.unwrap_or_default()))
}

async fn issue_tokens(state: &AppState, rid: &RequestId, user_id: &str) -> Result<Value, AppError> {
    let jti = uuid_hex32();
    let expires_at = {
        let expiry = chrono::Utc::now() + chrono::Duration::days(crate::security::REFRESH_DAYS);
        timefmt::py_isoformat(expiry.naive_utc())
    };
    store::store_refresh_token(&state.pool, rid, &jti, user_id, &expires_at).await?;
    Ok(json!({
        "access_token": create_token(user_id, state.settings.jwt_expiry_days, &state.settings.jwt_secret),
        "refresh_token": create_refresh_token(user_id, &jti, &state.settings.jwt_secret),
        "token_type": "bearer",
    }))
}

// ---- Endpoints ----

/// GET /auth/google/status — public, cho UI ẩn nút đăng nhập.
pub async fn status(State(state): State<AppState>) -> Json<Value> {
    Json(json!({ "enabled": state.settings.google_oauth_enabled() }))
}

/// GET /auth/google/start
pub async fn start(State(state): State<AppState>, rid: RequestId) -> Result<Response, AppError> {
    check_enabled(&state, &rid)?;
    let token = make_state(&state, "login", None);
    let nonce = decode_state_nonce(&state, &rid, &token)?;
    auth_redirect(&state, &rid, &token, &nonce)
}

/// GET /auth/google/link + /auth/google/link/start
pub async fn link_start(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
) -> Result<Response, AppError> {
    check_enabled(&state, &rid)?;
    let token = make_state(&state, "link", Some(&user.id));
    let nonce = decode_state_nonce(&state, &rid, &token)?;
    auth_redirect(&state, &rid, &token, &nonce)
}

/// Lấy nonce từ state vừa tạo (Python decode lại JWT trong _auth_redirect).
fn decode_state_nonce(state: &AppState, rid: &RequestId, token: &str) -> Result<String, AppError> {
    let claims: StateClaims = crate::security::decode_hs256(token, &state.settings.jwt_secret)
        .ok_or_else(|| internal_err(rid, "state decode failed"))?;
    Ok(claims.nonce)
}

/// GET /auth/google/callback?code&state
pub async fn callback(
    State(state): State<AppState>,
    rid: RequestId,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Response, AppError> {
    // code: str, state: str bắt buộc ở query — thiếu → 422 như FastAPI
    let mut fields = Vec::new();
    let code = match params.get("code") {
        Some(c) => c.clone(),
        None => {
            crate::validation::push_query_field(&mut fields, "code");
            String::new()
        }
    };
    let state_param = match params.get("state") {
        Some(s) => s.clone(),
        None => {
            crate::validation::push_query_field(&mut fields, "state");
            String::new()
        }
    };
    finish(fields)?;

    check_enabled(&state, &rid)?;
    let claims = decode_state(&state, &rid, &state_param)?;
    let (provider_id, email, _name) = google_identity(&state, &rid, &code, &claims.nonce).await?;
    let existing = store::get_oauth_account(&state.pool, &rid, "google", &provider_id).await?;

    if claims.action == "link" {
        let Some(user_id) = claims.sub.clone() else {
            return Err(AppError::new(
                "INVALID_OAUTH_STATE",
                "OAuth state is invalid.",
                StatusCode::BAD_REQUEST,
            )
            .with_request_id(&rid));
        };
        if store::get_user_by_id(&state.pool, &rid, &user_id).await?.is_none() {
            return Err(AppError::new(
                "INVALID_OAUTH_STATE",
                "OAuth state is invalid.",
                StatusCode::BAD_REQUEST,
            )
            .with_request_id(&rid));
        }
        match &existing {
            Some(acc) if acc.user_id != user_id => {
                return Err(AppError::new(
                    "OAUTH_ALREADY_LINKED",
                    "Google account is already linked.",
                    StatusCode::CONFLICT,
                )
                .with_request_id(&rid));
            }
            Some(_) => {}
            None => {
                if let Err(err) =
                    store::link_oauth_account(&state.pool, &rid, &user_id, "google", &provider_id, &email)
                        .await
                {
                    if store::get_oauth_account(&state.pool, &rid, "google", &provider_id)
                        .await?
                        .is_some()
                    {
                        return Err(AppError::new(
                            "OAUTH_ALREADY_LINKED",
                            "Google account is already linked.",
                            StatusCode::CONFLICT,
                        )
                        .with_request_id(&rid));
                    }
                    return Err(err);
                }
            }
        }
        return Ok(Redirect::to("/?google_link=success").into_response());
    }

    let user_id = match &existing {
        Some(acc) => {
            if store::get_user_by_id(&state.pool, &rid, &acc.user_id).await?.is_none() {
                return Err(AppError::new(
                    "OAUTH_ACCOUNT_INVALID",
                    "Google account link is invalid.",
                    StatusCode::CONFLICT,
                )
                .with_request_id(&rid));
            }
            acc.user_id.clone()
        }
        None => {
            if store::get_user_by_email_ci(&state.pool, &rid, &email).await?.is_some() {
                return Ok(Redirect::to("/?google_error=email_exists").into_response());
            }
            let mut username = email.split('@').next().unwrap_or("").chars().take(54).collect::<String>();
            if username.is_empty() {
                username = "google-user".to_owned();
            }
            if store::get_user_by_username(&state.pool, &rid, &username).await?.is_some() {
                username = format!("{username}-{}", random_hex6());
            }
            let user = store::UserRow {
                id: crate::security::new_id(),
                username: username.clone(),
                password_hash: GOOGLE_ONLY_PASSWORD_HASH.to_owned(),
                created_at: timefmt::now_storage(),
                verified: false,
                email: email.clone(),
            };
            store::insert_user(&state.pool, &rid, &user).await?;
            store::set_verified(&state.pool, &rid, &user.id, true).await?;
            store::link_oauth_account(&state.pool, &rid, &user.id, "google", &provider_id, &email)
                .await?;
            user.id
        }
    };

    let exchange_code = crate::security::token_urlsafe_32();
    {
        let mut exchanges = state.oauth_exchanges.lock().expect("oauth exchanges lock");
        exchanges.insert(exchange_code.clone(), (user_id, now_f64() + EXCHANGE_TTL));
    }
    Ok(Redirect::to(&format!("/?google_code={exchange_code}")).into_response())
}

/// POST /auth/google/exchange
pub async fn exchange(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let code = match obj {
        Some(o) => req_str_field(o, "code", &mut fields),
        None => {
            crate::validation::push_field(&mut fields, "code");
            String::new()
        }
    };
    finish(fields)?;

    let invalid = || {
        AppError::new(
            "INVALID_OAUTH_CODE",
            "Google sign-in code is invalid or expired.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid)
    };
    let record = {
        let mut exchanges = state.oauth_exchanges.lock().expect("oauth exchanges lock");
        exchanges.remove(&code)
    };
    let Some((user_id, _expiry)) = record.filter(|(_, exp)| *exp >= now_f64()) else {
        return Err(invalid());
    };
    if store::get_user_by_id(&state.pool, &rid, &user_id).await?.is_none() {
        return Err(invalid());
    }
    Ok(Json(issue_tokens(&state, &rid, &user_id).await?))
}

/// DELETE /auth/google/unlink + DELETE /auth/google/link
pub async fn unlink(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
) -> AppResult<Json<Value>> {
    let accounts = store::list_oauth_accounts(&state.pool, &rid, &user.id).await?;
    if accounts.is_empty() {
        return Err(AppError::new(
            "OAUTH_NOT_LINKED",
            "Google account is not linked.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    }
    if user.password_hash.len() == GOOGLE_ONLY_PASSWORD_HASH.len()
        && user.password_hash.chars().all(|c| c == '!')
        && accounts.len() <= 1
    {
        return Err(AppError::new(
            "LAST_LOGIN_METHOD",
            "Cannot unlink the last login method.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    }
    if !store::unlink_oauth_account(&state.pool, &rid, &user.id, "google").await? {
        return Err(AppError::new(
            "OAUTH_NOT_LINKED",
            "Google account is not linked.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    }
    Ok(Json(json!({ "message": "Google account unlinked." })))
}
