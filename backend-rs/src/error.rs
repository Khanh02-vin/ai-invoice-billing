//! Hợp đồng lỗi JSON ổn định — mirror của src/errors.py.
//!
//! Body: `{"error": {"code", "message", "request_id", "details"?}}`.
//! Không trả stack trace hay dữ liệu nhạy cảm.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

/// Request ID do middleware gắn vào extensions mỗi request.
#[derive(Clone, Debug)]
pub struct RequestId(pub String);

#[derive(Debug)]
pub struct AppError {
    pub code: String,
    pub message: String,
    pub status: StatusCode,
    pub details: Option<Value>,
    pub request_id: String,
    /// Headers bổ sung (RateLimitExceeded: Retry-After, X-RateLimit-*)
    pub headers: Vec<(String, String)>,
}

impl AppError {
    pub fn new(code: &str, message: &str, status: StatusCode) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            status,
            details: None,
            request_id: crate::security::new_request_id(),
            headers: Vec::new(),
        }
    }

    /// RateLimitExceeded của Python: 429 + details + headers chuẩn.
    pub fn rate_limited(limit: i64, remaining: i64, retry_after: i64) -> Self {
        Self {
            code: "TOO_MANY_REQUESTS".into(),
            message: "Quá nhiều yêu cầu. Vui lòng thử lại sau.".into(),
            status: StatusCode::TOO_MANY_REQUESTS,
            details: Some(json!({
                "limit": limit,
                "remaining": remaining,
                "retry_after": retry_after,
            })),
            request_id: crate::security::new_request_id(),
            headers: vec![
                ("Retry-After".into(), retry_after.to_string()),
                ("X-RateLimit-Limit".into(), limit.to_string()),
                ("X-RateLimit-Remaining".into(), remaining.to_string()),
            ],
        }
    }

    pub fn with_request_id(mut self, rid: &RequestId) -> Self {
        self.request_id = rid.0.clone();
        self
    }

    /// 422 VALIDATION_ERROR — details.fields là danh sách loc [["body","amount"], …].
    pub fn validation(fields: Vec<Value>) -> Self {
        Self {
            code: "VALIDATION_ERROR".into(),
            message: "Dữ liệu đầu vào không hợp lệ.".into(),
            status: StatusCode::UNPROCESSABLE_ENTITY,
            details: Some(json!({ "fields": fields })),
            request_id: crate::security::new_request_id(),
            headers: Vec::new(),
        }
    }

    pub fn unauthorized(rid: &RequestId) -> Self {
        AppError::new("UNAUTHORIZED", "Cần đăng nhập.", StatusCode::UNAUTHORIZED)
            .with_request_id(rid)
    }
}

fn public_body(code: &str, message: &str, request_id: &str, details: Option<&Value>) -> Value {
    let mut error = json!({
        "code": code,
        "message": message,
        "request_id": request_id,
    });
    if let Some(details) = details {
        error["details"] = details.clone();
    }
    json!({ "error": error })
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let body = public_body(
            &self.code,
            &self.message,
            &self.request_id,
            self.details.as_ref(),
        );
        let mut response = (self.status, Json(body)).into_response();
        for (name, value) in &self.headers {
            if let (Ok(name), Ok(value)) = (
                name.parse::<axum::http::HeaderName>(),
                axum::http::HeaderValue::from_str(value),
            ) {
                response.headers_mut().insert(name, value);
            }
        }
        response
    }
}

pub type AppResult<T> = Result<T, AppError>;

/// 404 cho route không tồn tại — cùng shape với Starlette HTTPException 404.
pub async fn fallback_not_found() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(public_body("NOT_FOUND", "Not Found", &crate::security::new_request_id(), None)),
    )
        .into_response()
}

/// 405 cho method sai trên route có tồn tại.
pub async fn fallback_method_not_allowed() -> Response {
    (
        StatusCode::METHOD_NOT_ALLOWED,
        Json(public_body(
            "HTTP_405",
            "Method Not Allowed",
            &crate::security::new_request_id(),
            None,
        )),
    )
        .into_response()
}
