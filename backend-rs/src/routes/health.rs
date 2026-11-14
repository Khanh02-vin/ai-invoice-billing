//! Health/readiness — khớp /health và /ready của app.py.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::db;
use crate::routes::AppState;

/// Health check — process sống.
pub async fn health() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok", "version": "1.0.0" }))
}

/// Readiness check — DB trả lời được.
pub async fn ready(State(state): State<AppState>) -> Response {
    if db::ping(&state.pool).await {
        Json(json!({ "ready": true, "checks": { "database": true } })).into_response()
    } else {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "ready": false, "checks": { "database": false } })),
        )
            .into_response()
    }
}
