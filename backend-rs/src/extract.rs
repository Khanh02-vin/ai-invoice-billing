//! Extractors: RequestId và CurrentUser (bearer JWT → user, như api/deps.py).

use axum::extract::FromRequestParts;
use axum::http::header;
use axum::http::request::Parts;
use axum::http::StatusCode;

use crate::error::{AppError, RequestId};
use crate::routes::AppState;
use crate::security::decode_token;
use crate::store;

pub fn rid_from_parts(parts: &Parts) -> RequestId {
    parts
        .extensions
        .get::<RequestId>()
        .cloned()
        .unwrap_or_else(|| RequestId(crate::security::new_request_id()))
}

impl FromRequestParts<AppState> for RequestId {
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(
        parts: &mut Parts,
        _state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        Ok(rid_from_parts(parts))
    }
}

/// IP client đáng tin cậy — mirror _client_ip_from_scope của Python:
/// ưu tiên peer kết nối; chỉ dùng X-Forwarded-For (phần tử CUỐI) khi peer nằm
/// trong RATE_LIMIT_TRUSTED_PROXIES. Không có trusted proxy → XFF bị bỏ qua.
pub struct ClientIp(pub String);

impl FromRequestParts<AppState> for ClientIp {
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(
        parts: &mut Parts,
        _state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        let peer = parts
            .extensions
            .get::<axum::extract::ConnectInfo<std::net::SocketAddr>>()
            .map(|c| c.0.ip().to_string())
            .unwrap_or_else(|| "unknown".to_owned());

        let trusted: std::collections::HashSet<String> = std::env::var("RATE_LIMIT_TRUSTED_PROXIES")
            .unwrap_or_default()
            .split(',')
            .map(|p| p.trim().to_owned())
            .filter(|p| !p.is_empty())
            .collect();
        if trusted.is_empty() {
            return Ok(Self(peer));
        }
        let Some(xff) = parts.headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()) else {
            return Ok(Self(peer));
        };
        if !trusted.contains(&peer) {
            return Ok(Self(peer));
        }
        match xff.rsplit(',').next().map(|s| s.trim()).and_then(|s| s.parse::<std::net::IpAddr>().ok()) {
            Some(ip) => Ok(Self(ip.to_string())),
            None => Ok(Self(peer)),
        }
    }
}

/// User từ JWT bearer. 401 khi thiếu header hoặc token không hợp lệ —
/// khớp current_user() của Python (HTTPBearer auto_error=False).
pub struct CurrentUser {
    pub id: String,
    pub username: String,
    pub email: String,
    pub password_hash: String,
}

impl FromRequestParts<AppState> for CurrentUser {
    type Rejection = AppError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        let rid = rid_from_parts(parts);
        let unauthorized_invalid = || {
            AppError::new("UNAUTHORIZED", "Token không hợp lệ.", StatusCode::UNAUTHORIZED)
                .with_request_id(&rid)
        };
        // "Bearer <token>" — scheme không phân biệt hoa/thường như Starlette
        let token = parts
            .headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.split_once(' '))
            .filter(|(scheme, _)| scheme.eq_ignore_ascii_case("bearer"))
            .map(|(_, rest)| rest.trim());
        let Some(token) = token.filter(|t| !t.is_empty()) else {
            return Err(AppError::unauthorized(&rid));
        };
        let Some(user_id) = decode_token(token, &state.settings.jwt_secret) else {
            return Err(unauthorized_invalid());
        };
        let user = store::get_user_by_id(&state.pool, &rid, &user_id).await?;
        match user {
            Some(u) => Ok(Self {
                id: u.id,
                username: u.username,
                email: u.email,
                password_hash: u.password_hash,
            }),
            None => Err(unauthorized_invalid()),
        }
    }
}
