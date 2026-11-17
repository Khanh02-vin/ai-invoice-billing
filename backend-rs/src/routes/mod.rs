//! Router, state và middleware (request-id, security headers, CORS allowlist) —
//! mirror của src/app.py + src/middleware.py.

pub mod auth;
pub mod auth_ext;
pub mod discover;
pub mod google_oauth;
pub mod health;
pub mod places;
pub mod spends;

use axum::extract::Request;
use axum::http::{header, HeaderName, HeaderValue, Method};
use axum::middleware::Next;
use axum::response::Response;
use axum::routing::{delete, get, post};
use axum::{middleware, Router};
use sqlx::SqlitePool;
use std::collections::HashMap;
use tower_http::cors::CorsLayer;

use crate::error::{fallback_method_not_allowed, fallback_not_found, RequestId};
use crate::security;
use crate::settings::Settings;

#[derive(Clone)]
pub struct AppState {
    pub pool: SqlitePool,
    pub settings: Settings,
    pub http: reqwest::Client,
    pub limiter: std::sync::Arc<crate::rate_limit::RateLimiter>,
    /// State OAuth (nonce → action, expiry) — single-use, TTL 600s
    pub oauth_states: std::sync::Arc<std::sync::Mutex<HashMap<String, (String, f64)>>>,
    /// Exchange code → (user_id, expiry) — single-use, TTL 60s
    pub oauth_exchanges: std::sync::Arc<std::sync::Mutex<HashMap<String, (String, f64)>>>,
    /// Dev reset OTP: username → (code 6 số, user_id, expiry, attempts)
    pub dev_reset_codes:
        std::sync::Arc<std::sync::Mutex<HashMap<String, (String, String, f64, u32)>>>,
}

pub fn build_router(state: AppState) -> Router {
    let cors = build_cors(&state.settings.cors_origins);
    Router::new()
        .route("/health", get(health::health))
        .route("/ready", get(health::ready))
        .route("/auth/register", post(auth::register))
        .route("/auth/login", post(auth::login))
        .route("/auth/me", get(auth::me))
        .route("/auth/verify-email", post(auth_ext::verify_email))
        .route("/auth/verify-email/confirm", get(auth_ext::confirm_email_get))
        .route("/auth/resend-verification", post(auth_ext::resend_verification))
        .route("/auth/forgot-password", post(auth_ext::forgot_password))
        .route("/auth/reset-password", post(auth_ext::reset_password))
        .route(
            "/auth/reset-password/confirm",
            get(auth_ext::reset_password_confirm),
        )
        .route("/auth/dev-reset-password", post(auth_ext::dev_reset_password))
        .route("/auth/dev-reset-password/request", post(auth_ext::dev_reset_request))
        .route("/auth/dev-reset-password/confirm", post(auth_ext::dev_reset_confirm))
        .route("/auth/refresh", post(auth_ext::refresh))
        .route("/auth/revoke", post(auth_ext::revoke))
        .route("/auth/google/status", get(google_oauth::status))
        .route("/auth/google/start", get(google_oauth::start))
        .route("/auth/google/link", get(google_oauth::link_start))
        .route("/auth/google/link/start", get(google_oauth::link_start))
        .route("/auth/google/callback", get(google_oauth::callback))
        .route("/auth/google/exchange", post(google_oauth::exchange))
        .route("/auth/google/unlink", delete(google_oauth::unlink))
        .route("/auth/google/link", delete(google_oauth::unlink))
        .route("/spends", post(spends::create_spend).get(spends::list_spends))
        .route("/spends/parse-qr", post(spends::parse_qr))
        .route(
            "/spends/drafts",
            post(spends::create_draft).get(spends::list_drafts),
        )
        .route("/spends/drafts/{draft_id}", delete(spends::delete_draft))
        .route(
            "/spends/drafts/{draft_id}/confirm",
            post(spends::confirm_draft),
        )
        .route("/spends/{spend_id}", delete(spends::delete_spend))
        .route(
            "/spends/{spend_id}/receipt",
            post(spends::upload_receipt).get(spends::get_receipt),
        )
        .route("/places", get(places::list_places).post(places::create_place))
        .route("/places/nearby", get(places::nearby))
        .route("/discover", get(discover::discover))
        .route("/discover/bands", get(discover::bands))
        .fallback(fallback_not_found)
        .method_not_allowed_fallback(fallback_method_not_allowed)
        // Thứ tự như Python: CORS ở ngoài cùng → request-id → security headers
        .layer(middleware::from_fn(security_headers_middleware))
        .layer(middleware::from_fn(request_id_middleware))
        .layer(cors)
        .with_state(state)
}

/// Gán request_id (client có thể truyền X-Request-ID qua) + echo về header.
async fn request_id_middleware(req: Request, next: Next) -> Response {
    let incoming = req
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
        .unwrap_or_else(security::new_request_id);
    let mut req = req;
    req.extensions_mut().insert(RequestId(incoming.clone()));
    let mut res = next.run(req).await;
    if let Ok(value) = HeaderValue::from_str(&incoming) {
        res.headers_mut().insert("x-request-id", value);
    }
    res
}

/// Security headers chuẩn — giống SecurityHeadersMiddleware của Python.
async fn security_headers_middleware(req: Request, next: Next) -> Response {
    let is_https = req.uri().scheme_str() == Some("https");
    let mut res = next.run(req).await;
    let headers = res.headers_mut();
    let statics: [(&str, &str); 5] = [
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("x-xss-protection", "1; mode=block"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        (
            "permissions-policy",
            "geolocation=(self), camera=(self), microphone=()",
        ),
    ];
    for (name, value) in statics {
        headers.insert(HeaderName::from_static(name), HeaderValue::from_static(value));
    }
    if is_https {
        headers.insert(
            HeaderName::from_static("strict-transport-security"),
            HeaderValue::from_static("max-age=31536000; includeSubDomains"),
        );
    }
    res
}

/// CORS theo danh sách origin cho phép — không wildcard trong production.
fn build_cors(origins: &[String]) -> CorsLayer {
    let list: Vec<HeaderValue> = origins
        .iter()
        .filter_map(|o| HeaderValue::from_str(o).ok())
        .collect();
    CorsLayer::new()
        .allow_origin(tower_http::cors::AllowOrigin::list(list))
        .allow_credentials(true)
        .allow_methods([
            Method::GET,
            Method::POST,
            Method::PUT,
            Method::DELETE,
            Method::OPTIONS,
        ])
        .allow_headers([
            header::AUTHORIZATION,
            header::CONTENT_TYPE,
            HeaderName::from_static("x-request-id"),
        ])
}
