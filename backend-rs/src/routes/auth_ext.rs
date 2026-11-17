//! Auth hardening router: refresh/revoke, email verification, password reset —
//! mirror src/routers/auth_ext.py.
//!
//! Endpoint phụ thuộc email fail-closed với HTTP 503 khi SMTP chưa cấu hình.
//! Token không bao giờ trả trong response.
//! Email-dependent endpoints fail closed with HTTP 503 when SMTP is unavailable.
//! Tokens are never returned in API responses.

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{Html, IntoResponse, Redirect, Response};
use axum::Json;
use serde_json::{json, Value};
use std::collections::HashMap;

use crate::error::{AppError, AppResult, RequestId};
use crate::extract::ClientIp;
use crate::mail;
use crate::routes::AppState;
use crate::security::{
    create_refresh_token, create_token, decode_refresh_token, hash_password, token_urlsafe_32,
};
use crate::store::{self, UserRow};
use crate::timefmt;
use crate::validation::{body_object, finish, parse_body, req_str_field};

// ---- Email helpers ----

/// (datetime.now(UTC) + N giờ).isoformat() của Python — lưu dạng aware string.
fn expiry_iso(hours: i64) -> String {
    let expiry = chrono::Utc::now() + chrono::Duration::hours(hours);
    timefmt::py_isoformat(expiry.naive_utc())
}

/// str(request.base_url).rstrip("/") của Python: APP_URL ưu tiên, không có thì
/// dựng từ Host header (scheme http khi đi thẳng uvicorn).
fn base_url(settings: &crate::settings::Settings, headers: &HeaderMap) -> String {
    if !settings.app_url.is_empty() {
        return settings.app_url.clone();
    }
    headers
        .get("host")
        .and_then(|v| v.to_str().ok())
        .map(|h| format!("http://{h}"))
        .unwrap_or_default()
}

/// Tạo token xác minh mới + gửi email kèm link bấm-một-lần. False nếu không gửi được.
async fn send_verification_email(
    state: &AppState,
    rid: &RequestId,
    user: &UserRow,
    base_url: &str,
) -> Result<bool, AppError> {
    let to = mail::user_email(&user.email, &user.username);
    if to.is_empty() || !state.settings.email_enabled() {
        return Ok(false);
    }
    let token = token_urlsafe_32();
    store::create_email_verification(&state.pool, rid, &user.id, &token, &expiry_iso(24)).await?;
    let mut lines = vec![
        format!("Xin chào {},", user.username),
        String::new(),
        "Bấm link sau để xác nhận địa chỉ email:".to_owned(),
    ];
    if !base_url.is_empty() {
        lines.push(format!("{base_url}/auth/verify-email/confirm?token={token}"));
    }
    lines.extend([
        String::new(),
        "Hoặc gửi mã này tới POST /auth/verify-email:".to_owned(),
        token.clone(),
        String::new(),
        "Link/mã hết hạn sau 24 giờ.".to_owned(),
    ]);
    Ok(mail::send_mail(
        &state.settings,
        &to,
        "Xác nhận email — Invoice & Billing",
        &lines.join("\n"),
    )
    .await)
}

/// Rate limit dependency của FastAPI — chạy SAU body validation, TRƯỚC handler
/// logic; vượt giới hạn → 429 với headers chuẩn.
fn check_rate_limit(
    state: &AppState,
    rid: &RequestId,
    client_ip: &str,
    policy_name: &str,
) -> Result<(), AppError> {
    let (max, window) = state.settings.rate_limit_policy(policy_name);
    let key = crate::rate_limit::make_key(client_ip, policy_name, None);
    if !state.limiter.is_allowed(&key, max, window as f64) {
        let remaining = state.limiter.get_remaining(&key, max, window as f64);
        return Err(AppError::rate_limited(max, remaining, window).with_request_id(rid));
    }
    Ok(())
}

fn parse_single_field(body: &[u8], key: &str) -> Result<String, AppError> {
    let mut fields = Vec::new();
    let parsed = parse_body(body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let value = match obj {
        Some(o) => req_str_field(o, key, &mut fields),
        None => {
            crate::validation::push_field(&mut fields, key);
            String::new()
        }
    };
    finish(fields)?;
    Ok(value)
}

// ---- Endpoints ----

/// POST /auth/verify-email — verify email via token. Marks user as verified.
pub async fn verify_email(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let token = parse_single_field(&body, "token")?;
    let row = store::get_email_verification(&state.pool, &rid, &token).await?;
    let valid = match &row {
        Some(r) => !r.used
            && timefmt::parse_expiry(&r.expires_at)
                .map(|exp| exp > chrono::Utc::now())
                .unwrap_or(false),
        None => false,
    };
    let Some(row) = row.filter(|_| valid) else {
        return Err(AppError::new(
            "INVALID_TOKEN",
            "Token xac nhan email khong hop le hoac da het han.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    };
    store::mark_email_verification_used(&state.pool, &rid, &row.id).await?;
    store::set_verified(&state.pool, &rid, &row.user_id, true).await?;
    Ok(Json(json!({ "message": "Da xac nhan email thanh cong." })))
}

/// GET /auth/verify-email/confirm — endpoint bạn bấm trong email link.
pub async fn confirm_email_get(
    State(state): State<AppState>,
    rid: RequestId,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    // token: str bắt buộc ở query — thiếu → 422 như FastAPI
    let Some(token) = params.get("token") else {
        let mut fields = Vec::new();
        crate::validation::push_query_field(&mut fields, "token");
        return AppError::validation(fields).into_response();
    };
    let row = store::get_email_verification(&state.pool, &rid, token).await.unwrap_or(None);
    let valid = match &row {
        Some(r) => !r.used
            && timefmt::parse_expiry(&r.expires_at)
                .map(|exp| exp > chrono::Utc::now())
                .unwrap_or(false),
        None => false,
    };
    match row.filter(|_| valid) {
        Some(row) => {
            // consume_email_verification đã SET verified=1 + đánh dấu used -> single-use.
            let _ = store::mark_email_verification_used(&state.pool, &rid, &row.id).await;
            let _ = store::set_verified(&state.pool, &rid, &row.user_id, true).await;
            verify_page("Xác nhận thành công", "Email đã được xác minh. Bạn có thể đăng nhập.", true)
        }
        None => verify_page("Xác minh thất bại", "Token không hợp lệ hoặc đã hết hạn.", false),
    }
}

/// HTML trả cho link trong email — byte-identical với _verify_page của Python.
fn verify_page(title: &str, body: &str, ok: bool) -> Response {
    let emoji = if ok { "✅" } else { "❌" };
    let html = format!(
        r#"<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title></head>
<body style="font-family:sans-serif;max-width:420px;margin:40px auto;text-align:center">
<h1>{emoji} {title}</h1><p>{body}</p>
</body></html>"#,
    );
    let status = if ok { StatusCode::OK } else { StatusCode::BAD_REQUEST };
    (status, Html(html)).into_response()
}

/// POST /auth/resend-verification — tạo + gửi email xác minh thật (503 nếu chưa có SMTP).
pub async fn resend_verification(
    State(state): State<AppState>,
    rid: RequestId,
    ip: ClientIp,
    headers: HeaderMap,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let username = parse_single_field(&body, "username")?;
    check_rate_limit(&state, &rid, &ip.0, "rate_limit_auth_resend")?;
    let settings = &state.settings;
    if !settings.email_enabled() {
        return Err(AppError::new(
            "EMAIL_SERVICE_UNAVAILABLE",
            "Email service is unconfigured.",
            StatusCode::SERVICE_UNAVAILABLE,
        )
        .with_request_id(&rid));
    }
    let user = store::get_user_by_username(&state.pool, &rid, &username).await?;
    let Some(user) = user else {
        return Err(AppError::new(
            "NOT_FOUND",
            "Ten nguoi dung khong ton tai.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    };
    if user.verified {
        return Err(AppError::new(
            "ALREADY_VERIFIED",
            "Tai khoan da duoc xac nhan.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    }
    let base = base_url(settings, &headers);
    let sent = send_verification_email(&state, &rid, &user, &base).await?;
    if !sent {
        return Err(AppError::new(
            "EMAIL_SEND_FAILED",
            "Khong gui duoc email xac nhan, thu lai sau.",
            StatusCode::BAD_GATEWAY,
        )
        .with_request_id(&rid));
    }
    Ok(Json(json!({ "message": "Email xac nhan da duoc gui qua email." })))
}

/// POST /forgot-password — best-effort: phản hồi luôn giống nhau để không lộ
/// username có tồn tại hay không.
pub async fn forgot_password(
    State(state): State<AppState>,
    rid: RequestId,
    client_ip: ClientIp,
    body: Bytes,
) -> AppResult<Json<Value>> {
    // Giới hạn số lần yêu cầu reset — tránh flooding Gmail (bị drop/spam) và
    // tránh lạm dụng dò tài khoản. Mặc định 3 lần / giờ cho mỗi IP.
    check_rate_limit(&state, &rid, &client_ip.0, "rate_limit_auth_forgot")?;
    let username = parse_single_field(&body, "username")?;
    let settings = &state.settings;
    if !settings.email_enabled() {
        return Err(AppError::new(
            "EMAIL_SERVICE_UNAVAILABLE",
            "Email service is unconfigured.",
            StatusCode::SERVICE_UNAVAILABLE,
        )
        .with_request_id(&rid));
    }
    let generic = json!({
        "message": "Neu ten nguoi dung ton tai, email dat lai mat khau se duoc gui qua email."
    });
    let Some(user) = store::get_user_by_username(&state.pool, &rid, &username).await? else {
        // Don't reveal whether username exists.
        return Ok(Json(generic));
    };
    let token = token_urlsafe_32();
    store::create_password_reset(&state.pool, &rid, &user.id, &token, &expiry_iso(1)).await?;

    // Chế độ demo/self-host: không có SMTP uy tín → trả mã ngay cho client
    // hiển thị trên màn hình. (Bị cấm ở production bởi ensure_production_readiness.)
    if settings.reset_code_mode == "show" {
        return Ok(Json(json!({
            "message": "Ma dat lai mat khau (che do demo).",
            "dev_code": token,
            "expires_minutes": 60,
        })));
    }

    let to = mail::user_email(&user.email, &user.username);
    if !to.is_empty() {
        let base = if settings.app_url.is_empty() {
            format!("http://localhost:{}", settings.port)
        } else {
            settings.app_url.trim_end_matches('/').to_owned()
        };
        let reset_link = format!("{base}/auth/reset-password/confirm?token={token}");
        let body_text = format!(
            "Xin chào {},\n\nBấm link sau để đặt lại mật khẩu:\n{reset_link}\n\nHoặc dán mã này vào app: {token}\n\nLink/mã hết hạn sau 1 giờ, dùng một lần. Nếu bạn không yêu cầu, hãy bỏ qua email này.",
            user.username
        );
        // Gửi trong background: kết nối SMTP + STARTTLS + login tốn ~3s với Gmail.
        // Không bắt user đợi — trả response ngay, email đi sau. Token đã được lưu
        // vào DB ở trên nên vẫn hợp lệ dù task chạy sau khi client đã nhận response.
        let mail_settings = settings.clone();
        let subject = "Đặt lại mật khẩu — Quanh Ta".to_owned();
        tokio::spawn(async move {
            if !mail::send_mail(&mail_settings, &to, &subject, &body_text).await {
                eprintln!("[quanh-ta] gui email reset that bai (da luu token trong DB)");
            }
        });
    }
    // Keep the response generic whether or not the user exists.
    Ok(Json(generic))
}

/// POST /reset-password — reset password via token.
pub async fn reset_password(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (token, new_password) = match obj {
        Some(o) => (
            req_str_field(o, "token", &mut fields),
            req_str_field(o, "new_password", &mut fields),
        ),
        None => {
            crate::validation::push_field(&mut fields, "token");
            crate::validation::push_field(&mut fields, "new_password");
            (String::new(), String::new())
        }
    };
    finish(fields)?;

    let row = store::get_password_reset(&state.pool, &rid, &token).await?;
    let valid = match &row {
        Some(r) => !r.used
            && timefmt::parse_expiry(&r.expires_at)
                .map(|exp| exp > chrono::Utc::now())
                .unwrap_or(false),
        None => false,
    };
    let Some(row) = row.filter(|_| valid) else {
        return Err(AppError::new(
            "INVALID_TOKEN",
            "Token dat lai mat khau khong hop le hoac da het han.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    };
    store::mark_password_reset_used(&state.pool, &rid, &row.id).await?;
    store::update_password(&state.pool, &rid, &row.user_id, &hash_password(&new_password))
        .await?;
    // Thu hồi toàn bộ refresh token của user sau khi đổi mật khẩu
    store::revoke_all_user_refresh_tokens(&state.pool, &rid, &row.user_id).await?;
    Ok(Json(json!({ "message": "Dat lai mat khau thanh cong." })))
}

/// GET /auth/reset-status?username=... — có mã reset còn hạn không (chỉ bool).
pub async fn reset_status(
    State(state): State<AppState>,
    rid: RequestId,
    Query(params): Query<HashMap<String, String>>,
) -> AppResult<Json<Value>> {
    let username = params.get("username").cloned().unwrap_or_default();
    let active = match store::get_user_by_username(&state.pool, &rid, &username).await? {
        Some(user) => store::has_active_reset(&state.pool, &rid, &user.id).await?,
        None => false,
    };
    Ok(Json(json!({ "has_active_code": active })))
}

/// GET /auth/reset-password/confirm — bấm link trong email → redirect về app
/// với token trên query. Không consume token ở đây (chỉ consume khi submit mật khẩu mới).
pub async fn reset_password_confirm(Query(params): Query<HashMap<String, String>>) -> Response {
    let token = params.get("token").cloned().unwrap_or_default();
    let encoded = percent_encoding::utf8_percent_encode(&token, percent_encoding::NON_ALPHANUMERIC)
        .to_string();
    Redirect::temporary(&format!("/?reset_token={encoded}")).into_response()
}

/// POST /auth/dev-reset-password — đặt lại mật khẩu TRỰC TIẾP không cần email.
/// CHỈ tồn tại ở chế độ dev: production trả 404 (không lộ endpoint) — nếu không,
/// bất kỳ ai vào được trang đăng nhập đều đổi được mật khẩu mọi tài khoản.
pub async fn dev_reset_password(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    // Chỉ bật khi operator đặt ALLOW_DEV_PASSWORD_RESET=1 — mặc định ẩn (404)
    // kể cả khi app chạy dev, để không ai quên tắt rồi để hở lỗ đổi mật khẩu.
    if !state.settings.dev_password_reset {
        return Err(AppError::new(
            "NOT_FOUND",
            "Not Found",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    }
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (username, new_password) = match obj {
        Some(o) => (
            req_str_field(o, "username", &mut fields),
            req_str_field(o, "new_password", &mut fields),
        ),
        None => {
            crate::validation::push_field(&mut fields, "username");
            crate::validation::push_field(&mut fields, "new_password");
            (String::new(), String::new())
        }
    };
    finish(fields)?;

    // Tolerant: gõ "khanh" không dấu vẫn ra "Khánh"
    let user = store::get_user_by_username_tolerant(&state.pool, &rid, &username).await?;
    let Some(user) = user else {
        return Err(AppError::new(
            "NOT_FOUND",
            "Ten nguoi dung khong ton tai.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    };
    store::update_password(&state.pool, &rid, &user.id, &hash_password(&new_password)).await?;
    // Thu hồi toàn bộ refresh token như reset-password thường
    store::revoke_all_user_refresh_tokens(&state.pool, &rid, &user.id).await?;
    Ok(Json(json!({ "message": "Dat lai mat khau thanh cong." })))
}

/// Mã OTP 6 số đều nhau từ 4 byte random (mod 1_000_000, đệm số 0).
fn six_digit_code() -> String {
    let mut buf = [0u8; 4];
    getrandom::getrandom(&mut buf).expect("hệ thống hết entropy");
    let n = u32::from_be_bytes(buf) % 1_000_000;
    format!("{n:06}")
}

const RESET_CODE_TTL: f64 = 600.0; // 10 phút
const RESET_CODE_MAX_ATTEMPTS: u32 = 5;

fn dev_reset_enabled(state: &AppState, rid: &RequestId) -> Result<(), AppError> {
    // Chỉ bật khi operator đặt ALLOW_DEV_PASSWORD_RESET=1 — mặc định ẩn (404)
    // kể cả khi app chạy dev, để không ai quên tắt rồi để hở lỗ đổi mật khẩu.
    if !state.settings.dev_password_reset {
        return Err(AppError::new(
            "NOT_FOUND",
            "Not Found",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(rid));
    }
    Ok(())
}

/// POST /auth/dev-reset-password/request — gửi mã OTP 6 số tới Gmail của tài
/// khoản (bước 1 của luồng dev đặt lại mật khẩu có xác nhận Gmail).
pub async fn dev_reset_request(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    dev_reset_enabled(&state, &rid)?;
    let username = parse_single_field(&body, "username")?;

    // Tolerant: gõ "khanh" không dấu vẫn ra "Khánh"
    let user = store::get_user_by_username_tolerant(&state.pool, &rid, &username).await?;
    let Some(user) = user else {
        return Err(AppError::new(
            "NOT_FOUND",
            "Ten nguoi dung khong ton tai.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    };
    let to = mail::user_email(&user.email, &user.username);
    if to.is_empty() {
        return Err(AppError::new(
            "NO_EMAIL",
            "Tai khoan chua co email de nhan ma xac nhan.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    }

    let code = six_digit_code();
    {
        let mut codes = state.dev_reset_codes.lock().expect("dev reset codes lock");
        codes.insert(
            username.clone(),
            (code.clone(), user.id.clone(), crate::routes::google_oauth::now_f64() + RESET_CODE_TTL, 0),
        );
    }
    let sent = mail::send_mail(
        &state.settings,
        &to,
        "Mã đặt lại mật khẩu — Invoice & Billing",
        &format!(
            "Xin chào {},\n\nMã xác nhận đặt lại mật khẩu: {}\n\nMã hết hạn sau 10 phút và chỉ dùng một lần. Nếu bạn không yêu cầu, hãy bỏ qua email này.",
            user.username, code
        ),
    )
    .await;
    if !sent {
        return Err(AppError::new(
            "EMAIL_SEND_FAILED",
            "Khong gui duoc email xac nhan, thu lai sau.",
            StatusCode::BAD_GATEWAY,
        )
        .with_request_id(&rid));
    }
    // Không log mã OTP: log server (docker logs / journal) là nơi nhiều người
    // đọc — lộ mã 6 số là chiếm được tài khoản trong 10 phút.
    Ok(Json(json!({
        "message": "Neu ten nguoi dung ton tai va co email, ma xac nhan da duoc gui qua email."
    })))
}

/// POST /auth/dev-reset-password/confirm — xác nhận mã OTP + mật khẩu mới.
pub async fn dev_reset_confirm(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    dev_reset_enabled(&state, &rid)?;
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (username, code, new_password) = match obj {
        Some(o) => (
            req_str_field(o, "username", &mut fields),
            req_str_field(o, "code", &mut fields),
            req_str_field(o, "new_password", &mut fields),
        ),
        None => {
            crate::validation::push_field(&mut fields, "username");
            crate::validation::push_field(&mut fields, "code");
            crate::validation::push_field(&mut fields, "new_password");
            (String::new(), String::new(), String::new())
        }
    };
    finish(fields)?;

    let invalid = || {
        AppError::new(
            "INVALID_RESET_CODE",
            "Ma xac nhan khong dung hoac da het han.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid)
    };
    let user_id = {
        // Khoá guard trong scope riêng — KHÔNG được sống qua bất kỳ .await nào
        let mut codes = state.dev_reset_codes.lock().expect("dev reset codes lock");
        let Some((expected_code, user_id, expiry, attempts)) = codes.get(&username).cloned() else {
            return Err(invalid());
        };
        if expiry < crate::routes::google_oauth::now_f64() {
            codes.remove(&username);
            return Err(invalid());
        }
        if code.trim() != expected_code {
            let attempts = attempts + 1;
            if attempts >= RESET_CODE_MAX_ATTEMPTS {
                codes.remove(&username);
                return Err(invalid());
            }
            codes.insert(username.clone(), (expected_code, user_id, expiry, attempts));
            return Err(invalid());
        }
        codes.remove(&username);
        user_id
    };

    store::update_password(&state.pool, &rid, &user_id, &hash_password(&new_password)).await?;
    store::revoke_all_user_refresh_tokens(&state.pool, &rid, &user_id).await?;
    Ok(Json(json!({ "message": "Dat lai mat khau thanh cong." })))
}

/// POST /refresh — issue new tokens via refresh token (rotation).
pub async fn refresh(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let refresh_token = parse_single_field(&body, "refresh_token")?;
    let settings = &state.settings;
    let Some((user_id, jti)) = decode_refresh_token(&refresh_token, &settings.jwt_secret) else {
        return Err(AppError::new(
            "INVALID_TOKEN",
            "Refresh token khong hop le.",
            StatusCode::UNAUTHORIZED,
        )
        .with_request_id(&rid));
    };
    let valid_user_id = store::valid_refresh_user(&state.pool, &rid, &jti).await?;
    if valid_user_id.as_deref() != Some(user_id.as_str()) {
        return Err(AppError::new(
            "INVALID_TOKEN",
            "Refresh token khong hop le hoac da thu hoi.",
            StatusCode::UNAUTHORIZED,
        )
        .with_request_id(&rid));
    }
    // Issue new refresh token (rotation).
    let new_jti = token_urlsafe_32();
    let expires_at = {
        let expiry =
            chrono::Utc::now() + chrono::Duration::days(crate::security::REFRESH_DAYS);
        timefmt::py_isoformat(expiry.naive_utc())
    };
    store::revoke_refresh_token(&state.pool, &rid, &jti).await?;
    store::store_refresh_token(&state.pool, &rid, &new_jti, &user_id, &expires_at).await?;
    Ok(Json(json!({
        "access_token": create_token(&user_id, settings.jwt_expiry_days, &settings.jwt_secret),
        "refresh_token": create_refresh_token(&user_id, &new_jti, &settings.jwt_secret),
    })))
}

/// POST /revoke — revoke a refresh token; token rác vẫn trả success để tránh
/// token enumeration.
pub async fn revoke(
    State(state): State<AppState>,
    rid: RequestId,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let refresh_token = parse_single_field(&body, "refresh_token")?;
    if let Some((_, jti)) =
        decode_refresh_token(&refresh_token, &state.settings.jwt_secret)
    {
        store::revoke_refresh_token(&state.pool, &rid, &jti).await?;
    }
    Ok(Json(json!({ "message": "Refresh token da duoc thu hoi." })))
}
