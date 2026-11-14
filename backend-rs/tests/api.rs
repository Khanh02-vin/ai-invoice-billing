//! API integration test — chạy toàn bộ router qua tower::ServiceExt::oneshot,
//! với DB SQLite tạm (tempfile). Kiểm tra contract JSON và flow chính.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use serde_json::{json, Value};
use tower::ServiceExt;

use quanh_ta_backend::db;
use quanh_ta_backend::routes::{build_router, AppState};
use quanh_ta_backend::settings::Settings;

struct TestCtx {
    app: Router,
    pool: sqlx::SqlitePool,
}

async fn make_ctx() -> TestCtx {
    make_ctx_opts(false).await
}

async fn make_ctx_opts(google_enabled: bool) -> TestCtx {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("test.db");
    // tempdir bị drop nếu trả về router thôi — giữ dir sống bằng Box leak
    let db_path: &'static str = Box::leak(db_path.to_string_lossy().into_owned().into_boxed_str());
    std::mem::forget(dir);
    let pool = db::connect(db_path).await.unwrap();
    db::ensure_schema(&pool).await.unwrap();
    // place để test spend
    sqlx::query("INSERT INTO places (id, name, normalized_name, category, address, lat, lon, created_at) VALUES ('placefixed123456', 'Quán Parity', 'quan parity', 'cafe', '1 Test', 10.776, 106.7, '2026-01-01 00:00:00.000000')")
        .execute(&pool)
        .await
        .unwrap();
    let settings = Settings {
        environment: "development".into(),
        jwt_secret: "test-secret-for-parity-only".into(),
        jwt_expiry_days: 7,
        open_registration: true,
        auto_verify_email: true,
        cors_origins: vec!["http://localhost:5173".into()],
        database_path: db_path.to_owned(),
        port: 0,
        smtp_host: String::new(),
        smtp_port: 0,
        smtp_user: String::new(),
        smtp_pass: String::new(),
        email_from: String::new(),
        app_url: String::new(),
        rate_limit_auth_login: "5:60".into(),
        rate_limit_auth_register: "3:60".into(),
        rate_limit_auth_resend: "3:300".into(),
        google_oauth_client_id: if google_enabled { "test-client-id.apps.googleusercontent.com".into() } else { String::new() },
        google_oauth_client_secret: if google_enabled { "test-secret".into() } else { String::new() },
        google_oauth_redirect_uri: if google_enabled { "http://localhost:5173/auth/google/callback".into() } else { String::new() },
    };
    let app = build_router(AppState {
        pool: pool.clone(),
        settings,
        http: reqwest::Client::new(),
        limiter: std::sync::Arc::new(quanh_ta_backend::rate_limit::RateLimiter::default()),
        oauth_states: std::sync::Arc::new(std::sync::Mutex::new(std::collections::HashMap::new())),
        oauth_exchanges: std::sync::Arc::new(std::sync::Mutex::new(std::collections::HashMap::new())),
    });
    TestCtx { app, pool }
}

async fn make_app() -> Router {
    make_ctx().await.app
}

async fn call(app: &Router, method: &str, uri: &str, token: Option<&str>, body: Option<Value>) -> (StatusCode, Value) {
    let (status, value, _headers) = call_full(app, method, uri, token, body).await;
    (status, value)
}

async fn call_full(
    app: &Router,
    method: &str,
    uri: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> (StatusCode, Value, axum::http::HeaderMap) {
    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(t) = token {
        builder = builder.header("authorization", format!("Bearer {t}"));
    }
    let req = builder
        .body(Body::from(body.map(|v| v.to_string()).unwrap_or_default()))
        .unwrap();
    let res = app.clone().oneshot(req).await.unwrap();
    let status = res.status();
    let headers = res.headers().clone();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX).await.unwrap();
    let value = if bytes.is_empty() {
        Value::Null
    } else {
        // HTML (verify-email/confirm) không phải JSON → trả Null thay vì panic
        serde_json::from_slice(&bytes).unwrap_or(Value::Null)
    };
    (status, value, headers)
}

#[tokio::test]
async fn health_and_ready() {
    let app = make_app().await;
    let (status, body) = call(&app, "GET", "/health", None, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"status": "ok", "version": "1.0.0"}));
    let (status, body) = call(&app, "GET", "/ready", None, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"ready": true, "checks": {"database": true}}));
}

#[tokio::test]
async fn auth_flow_register_login_me() {
    let app = make_app().await;

    // Body sai định dạng → 422 VALIDATION_ERROR có fields
    let (status, body) = call(&app, "POST", "/auth/register", None, Some(json!({"username": "ab"}))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "VALIDATION_ERROR");
    assert_eq!(body["error"]["details"]["fields"], json!([["body", "username"], ["body", "password"]]));
    assert!(body["error"]["request_id"].is_string());

    let (status, body) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "user_a", "password": "secret123", "email": "a@b.dev"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["token_type"], "bearer");
    assert_eq!(body["verification_sent"], false);
    assert_eq!(body["verified"], true); // auto_verify_email bật trong test
    assert!(body["access_token"].is_string());
    assert!(body["refresh_token"].is_string());
    let access = body["access_token"].as_str().unwrap();

    // Trùng username → 409
    let (status, body) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "user_a", "password": "secret123"})),
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"]["code"], "USERNAME_EXISTS");

    // Login bằng email (case-insensitive) → dùng được
    let (status, body) = call(
        &app,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "A@B.dev", "password": "secret123"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["access_token"].is_string());

    // Login sai mật khẩu → 401
    let (status, body) = call(
        &app,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "user_a", "password": "sai-roi"})),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "INVALID_CREDENTIALS");

    // /auth/me
    let (status, body) = call(&app, "GET", "/auth/me", Some(access), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["username"], "user_a");
    assert_eq!(body["email"], "a@b.dev");
    assert!(body["id"].is_string());

    // Token rác → 401
    let (status, body) = call(&app, "GET", "/auth/me", Some("khong-phai-jwt"), None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "UNAUTHORIZED");

    // Thiếu header → 401
    let (status, body) = call(&app, "GET", "/auth/me", None, None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "UNAUTHORIZED");
}

#[tokio::test]
async fn spends_flow() {
    let app = make_app().await;
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "spender", "password": "secret123"})),
    )
    .await;
    let token = reg["access_token"].as_str().unwrap();

    // Validation: amount 0 → 422
    let (status, body) = call(
        &app,
        "POST",
        "/spends",
        Some(token),
        Some(json!({"place_id": "placefixed123456", "amount": 0})),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["body", "amount"]]));

    // Place không tồn tại → 404
    let (status, body) = call(
        &app,
        "POST",
        "/spends",
        Some(token),
        Some(json!({"place_id": "khongtonai", "amount": 50000})),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "PLACE_NOT_FOUND");

    // Tạo spend bình thường
    let (status, body) = call(
        &app,
        "POST",
        "/spends",
        Some(token),
        Some(json!({
            "place_id": "placefixed123456",
            "amount": 125001,
            "party_size": 2,
            "note": "  cà phê  ",
            "occurred_at": "2026-11-14T10:30:00+07:00"
        })),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let spend = &body["spend"];
    // 125001/2 = 62500.5 → round() của Python (banker's) = 62500
    assert_eq!(spend["spend_per_person"], 62500);
    assert_eq!(spend["place_name"], "Quán Parity");
    assert_eq!(spend["category"], "cafe");
    // +07:00 được quy đổi UTC khi lưu như SQLAlchemy bind; response luôn +00:00
    assert_eq!(spend["occurred_at"], "2026-11-14T03:30:00+00:00");
    assert_eq!(spend["note"], "cà phê");
    assert_eq!(spend["spend_label"], "mức chi ghi nhận");
    let spend_id = spend["id"].as_str().unwrap();

    // List
    let (status, body) = call(&app, "GET", "/spends", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["spends"].as_array().unwrap().len(), 1);
    assert_eq!(body["spend_label"], "mức chi ghi nhận");

    // List với khoảng ngày hợp lệ
    let (status, _) = call(
        &app,
        "GET",
        "/spends?start=2026-11-01&end=2026-11-30",
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    // start > end → 400
    let (status, body) = call(
        &app,
        "GET",
        "/spends?start=2026-12-01&end=2026-11-01",
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_DATE_RANGE");

    // start sai định dạng → 422 loc ["query","start"]
    let (status, body) = call(&app, "GET", "/spends?start=ngay-sai", Some(token), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["query", "start"]]));

    // Xoá của người khác → 403; của mình → OK; xoá lần nữa → 404
    let (_, reg2) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "spender2", "password": "secret123"})),
    )
    .await;
    let token2 = reg2["access_token"].as_str().unwrap();
    let (status, body) = call(&app, "DELETE", &format!("/spends/{spend_id}"), Some(token2), None).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"]["code"], "FORBIDDEN");

    let (status, body) = call(&app, "DELETE", &format!("/spends/{spend_id}"), Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"deleted": true}));
    let (status, body) = call(&app, "DELETE", &format!("/spends/{spend_id}"), Some(token), None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "SPEND_NOT_FOUND");
}

#[tokio::test]
async fn drafts_flow() {
    let app = make_app().await;
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "draftuser", "password": "secret123"})),
    )
    .await;
    let token = reg["access_token"].as_str().unwrap();

    let (status, body) = call(
        &app,
        "POST",
        "/spends/drafts",
        Some(token),
        Some(json!({
            "amount": 125000,
            "merchant_name": "  Cơm Tám  ",
            "bank_bin": "693543",  // Pydantic validate độ dài TRƯỚC strip — có space là 422
            "account": "12345678",
            "lat": 10.776,
            "lon": 106.7
        })),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let draft = &body["draft"];
    assert_eq!(draft["merchant_name"], "Cơm Tám"); // đã strip
    assert_eq!(draft["bank_bin"], "693543");
    assert_eq!(draft["place_id"], Value::Null);
    assert!(draft["id"].is_string());
    let draft_id = draft["id"].as_str().unwrap();

    let (status, body) = call(&app, "GET", "/spends/drafts", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["drafts"].as_array().unwrap().len(), 1);

    let (status, body) = call(&app, "DELETE", &format!("/spends/drafts/{draft_id}"), Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"deleted": true}));

    // Sai user → 404 (khớp Python: delete_draft trả False → DRAFT_NOT_FOUND)
    let (_, reg2) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "draftuser2", "password": "secret123"})),
    )
    .await;
    let token2 = reg2["access_token"].as_str().unwrap();
    let (_, d2) = call(
        &app,
        "POST",
        "/spends/drafts",
        Some(token2),
        Some(json!({"amount": 50000})),
    )
    .await;
    let other_draft = d2["draft"]["id"].as_str().unwrap();
    let (status, body) = call(&app, "DELETE", &format!("/spends/drafts/{other_draft}"), Some(token), None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "DRAFT_NOT_FOUND");
}

#[tokio::test]
async fn parse_qr_endpoint() {
    let app = make_app().await;
    // Không cần token (public như Python)
    let (status, body) = call(
        &app,
        "POST",
        "/spends/parse-qr",
        None,
        Some(json!({"text": "00020101021138400010A00000072701220006693543010812345678530370454061250005802VN5909QUAN TEST6304ABCD"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["amount"], 125000);
    assert_eq!(body["bank_bin"], "693543");
    assert_eq!(body["account"], "12345678");
    assert_eq!(body["merchant_name"], "QUAN TEST");

    // text quá dài → 422
    let long_text = "x".repeat(4001);
    let (status, body) = call(&app, "POST", "/spends/parse-qr", None, Some(json!({ "text": long_text }))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["body", "text"]]));

    // Body rỗng → text mặc định "" → null hết
    let (status, body) = call(&app, "POST", "/spends/parse-qr", None, Some(json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["amount"], Value::Null);
    assert_eq!(body["raw"], "");
}

#[tokio::test]
async fn discover_flow() {
    let app = make_app().await;
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "explorer", "password": "secret123"})),
    )
    .await;
    let token = reg["access_token"].as_str().unwrap();

    // Place cafe gần + 3 lần chi → band thật (≥ MIN_SAMPLES)
    let (_, pa) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "Quán Điểm Cao", "category": "cafe", "lat": 10.776, "lon": 106.7})),
    )
    .await;
    let place_a = pa["place"]["id"].as_str().unwrap().to_owned();
    for amount in [30000, 50000, 70000] {
        let (status, _) = call(
            &app,
            "POST",
            "/spends",
            Some(token),
            Some(json!({"place_id": place_a, "amount": amount, "party_size": 1})),
        )
        .await;
        assert_eq!(status, StatusCode::OK);
    }
    // Place bar xa, chưa có chi → unpriced
    let (_, pb) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "Quán Xa Không Giá", "category": "bar", "lat": 10.785, "lon": 106.705})),
    )
    .await;
    let place_b = pb["place"]["id"].as_str().unwrap().to_owned();

    // /discover/bands — cafe có band thật p25=40000 p75=60000, median 50000;
    // bar không có default → low/high null; restaurant default
    let (status, body) = call(&app, "GET", "/discover/bands", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["weights"], json!({"price": 0.35, "distance": 0.3, "category": 0.2, "history": 0.15}));
    assert_eq!(body["min_samples"], 3);
    let rows = body["bands"].as_array().unwrap();
    assert_eq!(rows.len(), 6);
    let cafe = rows.iter().find(|r| r["category"] == "cafe").unwrap();
    assert_eq!(cafe["low"], 40000);
    assert_eq!(cafe["high"], 60000);
    assert_eq!(cafe["median"], 50000);
    assert_eq!(cafe["sample_size"], 3);
    assert_eq!(cafe["is_default"], false);
    let bar = rows.iter().find(|r| r["category"] == "bar").unwrap();
    assert_eq!(bar["low"], Value::Null);
    assert_eq!(bar["is_default"], false);
    let restaurant = rows.iter().find(|r| r["category"] == "restaurant").unwrap();
    assert_eq!(restaurant["low"], 50000);
    assert_eq!(restaurant["is_default"], true);

    // /discover với toạ độ — place A có score 1.0 (đúng band, đúng chỗ, quán quen)
    let (status, body) = call(
        &app,
        "GET",
        "/discover?lat=10.776&lon=106.7&radius_km=3",
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["bounds"], json!({"lat": 10.776, "lon": 106.7, "radius_km": 3.0}));
    let result = body["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == place_a)
        .unwrap();
    assert_eq!(result["score"], 1.0);
    assert_eq!(
        result["parts"],
        json!({"price": 1.0, "distance": 1.0, "category": 1.0, "history": 1.0})
    );
    assert_eq!(result["spend_per_person"], 50000);
    assert_eq!(result["band"], json!({"low": 40000, "high": 60000, "is_default": false}));
    assert_eq!(result["fits"], true);
    assert_eq!(result["visits"], 3);
    let unpriced_row = body["unpriced"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == place_b)
        .unwrap();
    assert_eq!(unpriced_row["spend_per_person"], Value::Null);
    assert_eq!(unpriced_row["score"], Value::Null);
    // place fixture (Quán Parity, chưa có spend) cũng nằm trong unpriced
    assert_eq!(body["counts"], json!({"priced": 1, "unpriced": 2}));

    // radius hẹp loại place B (cách ~1.2 km); place fixture cùng toạ độ → giữ
    let (status, body) = call(
        &app,
        "GET",
        "/discover?lat=10.776&lon=106.7&radius_km=0.001",
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["unpriced"].as_array().unwrap().iter().all(|r| r["id"] != place_b));
    assert_eq!(body["counts"]["unpriced"], 1);

    // Lọc category + q (normalize)
    let (status, body) = call(
        &app,
        "GET",
        "/discover?category=cafe&q=DIEM%20CAO",
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["results"].as_array().unwrap().len(), 1);
    assert_eq!(body["unpriced"].as_array().unwrap().len(), 0);

    // Chỉ lat không lon → 400 INVALID_COORDINATES
    let (status, body) = call(&app, "GET", "/discover?lat=10.776", Some(token), None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_COORDINATES");
    assert_eq!(body["error"]["message"], "Cần cả lat và lon.");

    // radius_km=0 (gt=0) → 422; limit=0 → 422; category sai → 400
    let (status, body) = call(&app, "GET", "/discover?radius_km=0", Some(token), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["query", "radius_km"]]));
    let (status, body) = call(&app, "GET", "/discover?limit=0", Some(token), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["query", "limit"]]));
    let (status, body) = call(&app, "GET", "/discover?category=spa", Some(token), None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_CATEGORY");

    // Cần đăng nhập
    let (status, body) = call(&app, "GET", "/discover/bands", None, None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "UNAUTHORIZED");
}

#[tokio::test]
async fn rate_limit_flow() {
    let ctx = make_ctx().await;
    let app = ctx.app.clone();

    // Đăng ký 3 user (giới hạn register 3:60), lần 4 → 429
    for i in 1..=3 {
        let (status, _) = call(
            &app,
            "POST",
            "/auth/register",
            None,
            Some(json!({"username": format!("rluser{i}"), "password": "secret123"})),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "register #{i}");
    }
    let (status, body, headers) = call_full(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "rluser4", "password": "secret123"})),
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(body["error"]["code"], "TOO_MANY_REQUESTS");
    assert_eq!(body["error"]["details"], json!({"limit": 3, "remaining": 0, "retry_after": 60}));
    assert_eq!(headers["retry-after"], "60");
    assert_eq!(headers["x-ratelimit-limit"], "3");
    assert_eq!(headers["x-ratelimit-remaining"], "0");

    // Body sai vẫn 422 (body validation TRƯỚC rate limit như FastAPI dependency)
    let app2 = make_app().await;
    for _ in 0..5 {
        let (status, _) = call(
            &app2,
            "POST",
            "/auth/login",
            None,
            Some(json!({"username": "nouser", "password": "wrongpw"})),
        )
        .await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
    let (status, body) = call(
        &app2,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "nouser", "password": "wrongpw"})),
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(body["error"]["details"]["limit"], 5);

    // Body sai định dạng → 422 chứ không 429
    let (status, body) = call(
        &app2,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "ab"})),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], "VALIDATION_ERROR");

    // Login sai body nhưng hết hạn rate limit vẫn 422 — prove thứ tự như trên
}

#[tokio::test]
async fn auth_ext_flow() {
    let ctx = make_ctx().await;
    let app = ctx.app.clone();

    // register + login lấy refresh token
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "extuser", "password": "cumatkhau1", "email": "ext@t.dev"})),
    )
    .await;
    assert_eq!(reg["verification_sent"], false); // SMTP chưa cấu hình
    let refresh_token = reg["refresh_token"].as_str().unwrap().to_owned();

    // /auth/refresh — rotation: trả token mới, thu hồi token cũ
    let (status, body) = call(
        &app,
        "POST",
        "/auth/refresh",
        None,
        Some(json!({"refresh_token": refresh_token})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["access_token"].is_string());
    assert!(body["refresh_token"].is_string());
    assert!(body["token_type"].is_null()); // TokenResponse chỉ có 2 field
    let new_refresh = body["refresh_token"].as_str().unwrap().to_owned();

    // refresh token cũ đã bị thu hồi → 401
    let (status, body) = call(
        &app,
        "POST",
        "/auth/refresh",
        None,
        Some(json!({"refresh_token": refresh_token})),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "INVALID_TOKEN");

    // /auth/revoke — token rác vẫn success (chống token enumeration)
    let (status, body) = call(
        &app,
        "POST",
        "/auth/revoke",
        None,
        Some(json!({"refresh_token": "token-rac"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["message"], "Refresh token da duoc thu hoi.");

    // revoke token thật → refresh sau đó 401
    let (status, _body) = call(
        &app,
        "POST",
        "/auth/revoke",
        None,
        Some(json!({"refresh_token": new_refresh})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, body) = call(
        &app,
        "POST",
        "/auth/refresh",
        None,
        Some(json!({"refresh_token": new_refresh})),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["message"], "Refresh token khong hop le hoac da thu hoi.");

    // /auth/verify-email token rác → 400
    let (status, body) = call(
        &app,
        "POST",
        "/auth/verify-email",
        None,
        Some(json!({"token": "sai-token"})),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_TOKEN");

    // /auth/resend-verification — SMTP chưa cấu hình → 503 (trước cả 404)
    let (status, body) = call(
        &app,
        "POST",
        "/auth/resend-verification",
        None,
        Some(json!({"username": "khongtonai"})),
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["error"]["code"], "EMAIL_SERVICE_UNAVAILABLE");

    // /auth/forgot-password — SMTP chưa cấu hình → 503
    let (status, body) = call(
        &app,
        "POST",
        "/auth/forgot-password",
        None,
        Some(json!({"username": "extuser"})),
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["error"]["code"], "EMAIL_SERVICE_UNAVAILABLE");

    // /auth/reset-password token rác → 400
    let (status, body) = call(
        &app,
        "POST",
        "/auth/reset-password",
        None,
        Some(json!({"token": "sai-token", "new_password": "matkhaumoi"})),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_TOKEN");

    // HTML confirm: chèn token hợp lệ trực tiếp vào DB → bấm link 1 lần
    let (_, user_row) = sqlx::query("SELECT id FROM users WHERE username = 'extuser'")
        .fetch_one(&ctx.pool)
        .await
        .map(|r| {
            let id: String = sqlx::Row::get(&r, "id");
            (StatusCode::OK, id)
        })
        .unwrap();
    sqlx::query(
        "INSERT INTO email_verifications (id, user_id, token, expires_at, used, created_at) \
         VALUES ('ev00000000000001', ?, 'htmltoken123', '2099-01-01T00:00:00+00:00', 0, '2026-01-01 00:00:00.000000')",
    )
    .bind(&user_row)
    .execute(&ctx.pool)
    .await
    .unwrap();

    let (status, _body, headers) = call_full(
        &app,
        "GET",
        "/auth/verify-email/confirm?token=htmltoken123",
        None,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(headers["content-type"].to_str().unwrap().starts_with("text/html"));
    // HTML parse: dùng raw call
    let req = Request::builder()
        .method("GET")
        .uri("/auth/verify-email/confirm?token=htmltoken123")
        .body(Body::empty())
        .unwrap();
    let res = app.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::BAD_REQUEST); // token đã used → single-use

    // Token thứ hai chưa dùng → HTML thành công 200
    sqlx::query(
        "INSERT INTO email_verifications (id, user_id, token, expires_at, used, created_at) \
         VALUES ('ev00000000000002', ?, 'htmltoken456', '2099-01-01T00:00:00+00:00', 0, '2026-01-01 00:00:00.000000')",
    )
    .bind(&user_row)
    .execute(&ctx.pool)
    .await
    .unwrap();
    let req = Request::builder()
        .method("GET")
        .uri("/auth/verify-email/confirm?token=htmltoken456")
        .body(Body::empty())
        .unwrap();
    let res = app.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);
    assert!(res.headers()["content-type"].to_str().unwrap().starts_with("text/html"));
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX).await.unwrap();
    let html = String::from_utf8(bytes.to_vec()).unwrap();
    assert!(html.contains("✅ Xác nhận thành công"));
    assert!(html.contains("Email đã được xác minh. Bạn có thể đăng nhập."));

    // user giờ verified — login không bị chặn EMAIL_NOT_VERIFIED
    let (status, _body) = call(
        &app,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "extuser", "password": "cumatkhau1"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    // /auth/reset-password thực: chèn reset token → đổi mật khẩu → token cũ login fail
    sqlx::query(
        "INSERT INTO password_resets (id, user_id, token, expires_at, used, created_at) \
         VALUES ('pr00000000000001', ?, 'resettoken789', '2099-01-01T00:00:00+00:00', 0, '2026-01-01 00:00:00.000000')",
    )
    .bind(&user_row)
    .execute(&ctx.pool)
    .await
    .unwrap();
    let (_, pre_reset) = call(
        &app,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "extuser", "password": "cumatkhau1"})),
    )
    .await;
    let old_refresh = pre_reset["refresh_token"].as_str().unwrap().to_owned();
    let (status, body) = call(
        &app,
        "POST",
        "/auth/reset-password",
        None,
        Some(json!({"token": "resettoken789", "new_password": "moimatkhau99"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["message"], "Dat lai mat khau thanh cong.");

    // mật khẩu cũ không dùng được, mới dùng được; refresh token cũ bị thu hồi
    let (status, _) = call(
        &app,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "extuser", "password": "cumatkhau1"})),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _) = call(
        &app,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "extuser", "password": "moimatkhau99"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, _) = call(
        &app,
        "POST",
        "/auth/refresh",
        None,
        Some(json!({"refresh_token": old_refresh})),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn unknown_route_and_method() {
    let app = make_app().await;
    let (status, body) = call(&app, "GET", "/khong-ton-tai", None, None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "NOT_FOUND");
    assert_eq!(body["error"]["message"], "Not Found");

    // /health chỉ có GET — POST → 405
    let (status, body) = call(&app, "POST", "/health", None, None).await;
    assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED);
    assert_eq!(body["error"]["code"], "HTTP_405");
}

#[tokio::test]
async fn security_headers_and_request_id() {
    let app = make_app().await;
    let req = Request::builder()
        .method("GET")
        .uri("/health")
        .header("x-request-id", "test-rid-001")
        .body(Body::empty())
        .unwrap();
    let res = app.oneshot(req).await.unwrap();
    assert_eq!(res.headers().get("x-request-id").unwrap(), "test-rid-001");
    assert_eq!(res.headers().get("x-content-type-options").unwrap(), "nosniff");
    assert_eq!(res.headers().get("x-frame-options").unwrap(), "DENY");
    assert_eq!(
        res.headers().get("permissions-policy").unwrap(),
        "geolocation=(self), camera=(self), microphone=()"
    );
}

#[tokio::test]
async fn places_flow() {
    let app = make_app().await;
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "placeman", "password": "secret123"})),
    )
    .await;
    let token = reg["access_token"].as_str().unwrap();

    // Danh sách trống ban đầu (place fixture chưa có stats)
    let (status, body) = call(&app, "GET", "/places", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["spend_label"], "mức chi ghi nhận");
    assert!(body["places"].as_array().unwrap().iter().all(|p| p["visits"] == 0 && p["priced"] == false));

    // Category sai → 400; name rỗng → 400 INVALID_NAME; name chỉ space → 400
    let (status, body) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "Quán X", "category": "spa"})),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_CATEGORY");

    let (status, body) = call(&app, "POST", "/places", Some(token), Some(json!({"name": "   "}))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_NAME");

    // name 121 ký tự → 422
    let (status, _) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "x".repeat(121)})),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);

    // lat sai khoảng → 422 loc ["body","lat"]
    let (status, body) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "Quán Y", "lat": 95.0})),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["body", "lat"]]));

    // Tạo mới — tên có dấu/space vẫn normalize được
    let (status, body) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "  Quán Cà Phê Mới  ", "category": "cafe", "lat": 10.8, "lon": 106.7})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["created"], true);
    assert_eq!(body["place"]["name"], "Quán Cà Phê Mới"); // đã strip
    assert_eq!(body["place"]["category"], "cafe");
    assert_eq!(body["place"]["priced"], false);
    assert_eq!(body["place"]["visits"], 0);
    assert_eq!(body["place"]["median_spend"], Value::Null);
    let first_id = body["place"]["id"].as_str().unwrap().to_owned();

    // Cùng tên + cùng toạ độ (≤50 m) → trả quán đã có, created=false
    let (status, body) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "quan ca phe moi", "lat": 10.8001, "lon": 106.7001})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["created"], false);
    assert_eq!(body["place"]["id"], first_id);

    // Cùng tên nhưng cách ~150 m → quán mới
    let (status, body) = call(
        &app,
        "POST",
        "/places",
        Some(token),
        Some(json!({"name": "Quán Cà Phê Mới", "lat": 10.8014, "lon": 106.7014})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["created"], true);
    assert_ne!(body["place"]["id"], first_id);

    // Search theo q (normalize) + category
    let (status, body) = call(&app, "GET", "/places?q=CA%20PHE", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["places"].as_array().unwrap().len(), 2);

    // category sai → 400
    let (status, body) = call(&app, "GET", "/places?category=karaoke", Some(token), None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_CATEGORY");

    // Query params sai → 422 loc ["query", ...]
    let (status, body) = call(&app, "GET", "/places?lat=200", Some(token), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["query", "lat"]]));
    let (status, body) = call(&app, "GET", "/places?radius_m=50", Some(token), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["query", "radius_m"]]));

    // Ghi chi tiêu tại place fixture rồi lọc giá
    let (status, _) = call(
        &app,
        "POST",
        "/spends",
        Some(token),
        Some(json!({"place_id": "placefixed123456", "amount": 125000, "party_size": 2})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let (status, body) = call(&app, "GET", "/places", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    let fixture = body["places"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["id"] == "placefixed123456")
        .unwrap();
    assert_eq!(fixture["visits"], 1);
    assert_eq!(fixture["median_spend"], 62500); // 1 mẫu: trả nguyên, không round_vnd
    assert_eq!(fixture["priced"], true);
    assert!(fixture["last_visit"].is_string());

    // median 62500 — lọc min_price=100000 loại nó đi
    let (status, body) = call(&app, "GET", "/places?min_price=100000", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["places"].as_array().unwrap().iter().all(|p| p["id"] != "placefixed123456"));

    // lọc max_price=100000 giữ lại
    let (status, body) = call(&app, "GET", "/places?max_price=100000", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["places"].as_array().unwrap().iter().any(|p| p["id"] == "placefixed123456"));

    // radius: place fixture ở (10.776, 106.7) — cách 10.7770,106.7 khoảng ~110 m > 100 m mặc định không loại (default 3000); dùng radius_m=100
    let (status, body) = call(
        &app,
        "GET",
        "/places?lat=10.7770&lon=106.7&radius_m=100",
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(body["places"].as_array().unwrap().iter().all(|p| p["id"] != "placefixed123456"));
}

#[tokio::test]
async fn confirm_draft_flow() {
    let app = make_app().await;
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "confirmer", "password": "secret123"})),
    )
    .await;
    let token = reg["access_token"].as_str().unwrap();

    // Draft không có place_id → confirm tạo place mới từ merchant_name
    let (_, d) = call(
        &app,
        "POST",
        "/spends/drafts",
        Some(token),
        Some(json!({"amount": 125000, "party_size": 2, "merchant_name": "  Cơm Tám  ",
                     "lat": 10.776, "lon": 106.7, "note": "ghi lúc quét"})),
    )
    .await;
    let draft_id = d["draft"]["id"].as_str().unwrap().to_owned();

    let (status, body) = call(&app, "POST", &format!("/spends/drafts/{draft_id}/confirm"), Some(token), Some(json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    let spend = &body["spend"];
    assert_eq!(spend["place_name"], "Cơm Tám"); // strip + làm tên place
    assert_eq!(spend["category"], "other"); // body không có → default
    assert_eq!(spend["amount"], 125000);
    assert_eq!(spend["spend_per_person"], 62500);
    assert_eq!(spend["note"], "ghi lúc quét"); // không có note trong body → lấy của draft
    let place_id_created = spend["place_id"].as_str().unwrap().to_owned();

    // Draft đã bị xoá sau confirm
    let (status, body) = call(&app, "GET", "/spends/drafts", Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["drafts"].as_array().unwrap().len(), 0);

    // Confirm lại → 404
    let (status, body) = call(&app, "POST", &format!("/spends/drafts/{draft_id}/confirm"), Some(token), Some(json!({}))).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "DRAFT_NOT_FOUND");

    // Draft thứ hai cùng tên + cùng toạ độ → ghim vào place ĐÃ TẠO (find_match)
    let (_, d2) = call(
        &app,
        "POST",
        "/spends/drafts",
        Some(token),
        Some(json!({"amount": 90000, "merchant_name": "com tam", "lat": 10.7761, "lon": 106.7001})),
    )
    .await;
    let draft2 = d2["draft"]["id"].as_str().unwrap().to_owned();
    let (status, body) = call(&app, "POST", &format!("/spends/drafts/{draft2}/confirm"), Some(token), Some(json!({"category": "restaurant", "note": "lần hai"}))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["spend"]["place_id"], place_id_created);
    assert_eq!(body["spend"]["note"], "lần hai");

    // Draft có place_id sẵn → dùng đúng place đó
    let (_, d3) = call(
        &app,
        "POST",
        "/spends/drafts",
        Some(token),
        Some(json!({"amount": 50000, "place_id": "placefixed123456"})),
    )
    .await;
    let draft3 = d3["draft"]["id"].as_str().unwrap().to_owned();
    let (status, body) = call(&app, "POST", &format!("/spends/drafts/{draft3}/confirm"), Some(token), Some(json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["spend"]["place_id"], "placefixed123456");
    assert_eq!(body["spend"]["place_name"], "Quán Parity");

    // User khác confirm draft của người khác → 403 (message riêng của confirm)
    let (_, reg2) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "confirmer2", "password": "secret123"})),
    )
    .await;
    let token2 = reg2["access_token"].as_str().unwrap();
    let (_, d4) = call(
        &app,
        "POST",
        "/spends/drafts",
        Some(token),
        Some(json!({"amount": 10000})),
    )
    .await;
    let draft4 = d4["draft"]["id"].as_str().unwrap().to_owned();
    let (status, body) = call(&app, "POST", &format!("/spends/drafts/{draft4}/confirm"), Some(token2), Some(json!({}))).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"]["code"], "FORBIDDEN");
    assert_eq!(body["error"]["message"], "Đây không phải giao dịch của bạn.");

    // Body sai định dạng → 422
    let (status, body) = call(
        &app,
        "POST",
        &format!("/spends/drafts/{draft4}/confirm"),
        Some(token),
        Some(json!({"category": "x".repeat(33)})),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["details"]["fields"], json!([["body", "category"]]));
}

#[tokio::test]
async fn google_oauth_flow() {
    // ---- Chưa cấu hình ----
    let app = make_app().await;
    let (status, body) = call(&app, "GET", "/auth/google/status", None, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"enabled": false}));

    let (status, body) = call(&app, "GET", "/auth/google/start", None, None).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["error"]["code"], "OAUTH_UNAVAILABLE");
    assert_eq!(body["error"]["message"], "Google OAuth is not configured.");

    // exchange không check enabled — code rác → 400 INVALID_OAUTH_CODE
    let (status, body) = call(
        &app,
        "POST",
        "/auth/google/exchange",
        None,
        Some(json!({"code": "code-khong-ton-tai"})),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_OAUTH_CODE");
    assert_eq!(body["error"]["message"], "Google sign-in code is invalid or expired.");

    // unlink khi chưa đăng nhập → 401; đã đăng nhập nhưng chưa link → 404
    let (status, _) = call(&app, "DELETE", "/auth/google/unlink", None, None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (_, reg) = call(
        &app,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "guser", "password": "secret123"})),
    )
    .await;
    let token = reg["access_token"].as_str().unwrap();
    let (status, body) = call(&app, "DELETE", "/auth/google/unlink", Some(token), None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "OAUTH_NOT_LINKED");

    // link/start chưa đăng nhập → 401
    let (status, _) = call(&app, "GET", "/auth/google/link", None, None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    // callback thiếu query params → 422
    let (status, body) = call(&app, "GET", "/auth/google/callback", None, None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        body["error"]["details"]["fields"],
        json!([["query", "code"], ["query", "state"]])
    );

    // ---- Đã cấu hình (giả) ----
    let ctx = make_ctx_opts(true).await;
    let app2 = ctx.app.clone();
    let (status, body) = call(&app2, "GET", "/auth/google/status", None, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"enabled": true}));

    // start → 307 redirect tới Google với state + nonce
    let (status, _body, headers) = call_full(&app2, "GET", "/auth/google/start", None, None).await;
    assert_eq!(status, StatusCode::TEMPORARY_REDIRECT);
    let location = headers["location"].to_str().unwrap();
    assert!(location.starts_with("https://accounts.google.com/o/oauth2/v2/auth?"));
    assert!(location.contains("client_id=test-client-id.apps.googleusercontent.com"));
    assert!(location.contains("response_type=code"));
    assert!(location.contains("scope=openid+email+profile"));
    assert!(location.contains("access_type=online"));
    assert!(location.contains("prompt=select_account"));
    assert!(location.contains("state=ey"));

    // link/start có đăng nhập → 307 (state chứa sub)
    let (_, reg2) = call(
        &app2,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "glinker", "password": "secret123"})),
    )
    .await;
    let token2 = reg2["access_token"].as_str().unwrap();
    let (status, _b, headers2) = call_full(&app2, "GET", "/auth/google/link", Some(token2), None).await;
    assert_eq!(status, StatusCode::TEMPORARY_REDIRECT);
    assert!(headers2["location"].to_str().unwrap().contains("state=ey"));

    // state rác ở callback → 400 INVALID_OAUTH_STATE (không đụng mạng)
    let (status, body) = call(
        &app2,
        "GET",
        "/auth/google/callback?code=abc&state=state-rac",
        None,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "INVALID_OAUTH_STATE");

    // OAuth-only user: password hash 60 '!' + 1 account → không được unlink
    let (status, _) = call(
        &app2,
        "POST",
        "/auth/register",
        None,
        Some(json!({"username": "googleonly", "password": "secret123"})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    // login LẤY TOKEN TRƯỚC khi ghi đè password hash thành google-only
    let (_, reg3) = call(
        &app2,
        "POST",
        "/auth/login",
        None,
        Some(json!({"username": "googleonly", "password": "secret123"})),
    )
    .await;
    let token3 = reg3["access_token"].as_str().unwrap().to_owned();
    sqlx::query("UPDATE users SET password_hash = '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' WHERE username = 'googleonly'")
        .execute(&ctx.pool)
        .await
        .unwrap();
    sqlx::query("INSERT INTO oauth_accounts (id, user_id, provider, provider_account_id, email, created_at) VALUES ('oa00000000000001', (SELECT id FROM users WHERE username='googleonly'), 'google', 'google-sub-1', 'go@t.dev', '2026-01-01 00:00:00.000000')")
        .execute(&ctx.pool)
        .await
        .unwrap();
    let (status, body) = call(&app2, "DELETE", "/auth/google/unlink", Some(&token3), None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"]["code"], "LAST_LOGIN_METHOD");
    assert_eq!(body["error"]["message"], "Cannot unlink the last login method.");

    // user có mật khẩu thật + 1 account → unlink OK; lần 2 → 404
    sqlx::query("INSERT INTO oauth_accounts (id, user_id, provider, provider_account_id, email, created_at) VALUES ('oa00000000000002', (SELECT id FROM users WHERE username='glinker'), 'google', 'google-sub-2', 'gl@t.dev', '2026-01-01 00:00:00.000000')")
        .execute(&ctx.pool)
        .await
        .unwrap();
    let (status, body) = call(&app2, "DELETE", "/auth/google/unlink", Some(token2), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["message"], "Google account unlinked.");
    let (status, body) = call(&app2, "DELETE", "/auth/google/unlink", Some(token2), None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"]["code"], "OAUTH_NOT_LINKED");
}
