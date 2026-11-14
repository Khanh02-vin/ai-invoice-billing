//! Auth: register / login / me — mirror các handler trong src/app.py.

use axum::body::Bytes;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::Json;
use serde_json::{json, Value};

use crate::error::{AppError, AppResult, RequestId};
use crate::extract::{ClientIp, CurrentUser};
use crate::routes::AppState;
use crate::security::{
    create_refresh_token, create_token, hash_password, new_id, new_jti, verify_password,
};
use crate::store::{self, UserRow};
use crate::timefmt;
use crate::validation::{body_object, check_len, finish, opt_str_field, parse_body, str_field};

/// str(request.base_url).rstrip("/") của Python — APP_URL ưu tiên, không có thì
/// dựng từ Host header.
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
    let to = crate::mail::user_email(&user.email, &user.username);
    if to.is_empty() || !state.settings.email_enabled() {
        return Ok(false);
    }
    let token = crate::security::token_urlsafe_32();
    let expires_at = {
        let expiry = chrono::Utc::now() + chrono::Duration::hours(24);
        timefmt::py_isoformat(expiry.naive_utc())
    };
    store::create_email_verification(&state.pool, rid, &user.id, &token, &expires_at).await?;
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
    Ok(crate::mail::send_mail(
        &state.settings,
        &to,
        "Xác nhận email — Invoice & Billing",
        &lines.join("\n"),
    )
    .await)
}

/// Rate limit dependency của FastAPI — chạy sau body validation, trước handler logic.
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

fn parse_user_create(body: &[u8]) -> Result<(String, String, Option<String>), AppError> {
    let mut fields = Vec::new();
    let parsed = parse_body(body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (username, password, email) = match obj {
        Some(o) => (
            str_field(o, "username", "", &mut fields),
            str_field(o, "password", "", &mut fields),
            opt_str_field(o, "email", &mut fields),
        ),
        None => (String::new(), String::new(), None),
    };
    check_len(&username, "username", 3, 64, &mut fields);
    check_len(&password, "password", 6, 128, &mut fields);
    if let Some(e) = &email {
        check_len(e, "email", 0, 254, &mut fields);
    }
    finish(fields)?;
    Ok((username, password, email))
}

fn invalid_credentials(rid: &RequestId) -> AppError {
    AppError::new(
        "INVALID_CREDENTIALS",
        "Sai tên người dùng hoặc mật khẩu.",
        StatusCode::UNAUTHORIZED,
    )
    .with_request_id(rid)
}

/// (datetime.now(UTC) + 30 ngày).isoformat() của Python — lưu vào refresh_tokens.
fn refresh_expiry() -> String {
    let expiry = chrono::Utc::now() + chrono::Duration::days(crate::security::REFRESH_DAYS);
    timefmt::py_isoformat(expiry.naive_utc())
}

/// POST /auth/register — Đăng ký mặc định TẮT (ai có link public đều gọi được API).
pub async fn register(
    State(state): State<AppState>,
    rid: RequestId,
    ip: ClientIp,
    headers: HeaderMap,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let (username, password, email) = parse_user_create(&body)?;
    check_rate_limit(&state, &rid, &ip.0, "rate_limit_auth_register")?;
    let settings = &state.settings;
    if !settings.open_registration {
        return Err(AppError::new(
            "REGISTRATION_CLOSED",
            "Đăng ký đang tắt.",
            StatusCode::FORBIDDEN,
        )
        .with_request_id(&rid));
    }
    if store::get_user_by_username(&state.pool, &rid, &username)
        .await?
        .is_some()
    {
        return Err(AppError::new(
            "USERNAME_EXISTS",
            "Tên người dùng đã tồn tại.",
            StatusCode::CONFLICT,
        )
        .with_request_id(&rid));
    }
    if let Some(e) = &email {
        if !e.is_empty() && !e.contains('@') {
            return Err(AppError::new(
                "INVALID_EMAIL",
                "Email không hợp lệ.",
                StatusCode::BAD_REQUEST,
            )
            .with_request_id(&rid));
        }
    }
    let user = UserRow {
        id: new_id(),
        username: username.clone(),
        password_hash: hash_password(&password),
        created_at: timefmt::now_storage(),
        verified: false,
        email: email.clone().unwrap_or_default(),
    };
    store::insert_user(&state.pool, &rid, &user).await?;
    // Demo không có SMTP: AUTO_VERIFY_EMAIL xác minh luôn để lần đăng nhập sau
    // không bị chặn EMAIL_NOT_VERIFIED (khớp app.py).
    if settings.auto_verify_email {
        store::set_verified(&state.pool, &rid, &user.id, true).await?;
    }
    let jti = new_jti();
    let refresh = create_refresh_token(&user.id, &jti, &settings.jwt_secret);
    store::store_refresh_token(&state.pool, &rid, &jti, &user.id, &refresh_expiry()).await?;
    // Gửi email xác minh ngay (best-effort): SMTP chưa cấu hình hoặc user không
    // có email -> verification_sent=False.
    let mut verification_sent = false;
    if !user.email.is_empty() {
        let base = base_url(settings, &headers);
        verification_sent = send_verification_email(&state, &rid, &user, &base).await?;
    }
    Ok(Json(json!({
        "access_token": create_token(&user.id, settings.jwt_expiry_days, &settings.jwt_secret),
        "token_type": "bearer",
        "refresh_token": refresh,
        "verification_sent": verification_sent,
        "verified": settings.auto_verify_email,
    })))
}

/// POST /auth/login
pub async fn login(
    State(state): State<AppState>,
    rid: RequestId,
    ip: ClientIp,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let (username, password, _email) = parse_user_create(&body)?;
    check_rate_limit(&state, &rid, &ip.0, "rate_limit_auth_login")?;
    let settings = &state.settings;
    let mut user = store::get_user_by_username(&state.pool, &rid, &username).await?;
    if user.is_none() && username.contains('@') {
        user = store::get_user_by_email_ci(
            &state.pool,
            &rid,
            &username.trim().to_lowercase(),
        )
        .await?;
    }
    let Some(user) = user else {
        return Err(invalid_credentials(&rid));
    };
    if !verify_password(&password, &user.password_hash) {
        return Err(invalid_credentials(&rid));
    }
    if !user.verified {
        return Err(AppError::new(
            "EMAIL_NOT_VERIFIED",
            "Vui lòng xác minh email trước.",
            StatusCode::FORBIDDEN,
        )
        .with_request_id(&rid));
    }
    let jti = new_jti();
    let refresh = create_refresh_token(&user.id, &jti, &settings.jwt_secret);
    store::store_refresh_token(&state.pool, &rid, &jti, &user.id, &refresh_expiry()).await?;
    Ok(Json(json!({
        "access_token": create_token(&user.id, settings.jwt_expiry_days, &settings.jwt_secret),
        "token_type": "bearer",
        "refresh_token": refresh,
        "verification_sent": false,
        "verified": false,
    })))
}

/// GET /auth/me
pub async fn me(user: CurrentUser) -> Json<Value> {
    Json(json!({
        "id": user.id,
        "username": user.username,
        "email": user.email,
    }))
}
