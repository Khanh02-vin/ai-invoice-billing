//! Điểm vào: settings → pool → schema → serve. Fail-fast như app.py import.

use quanh_ta_backend::{db, routes::{build_router, AppState}, settings::Settings};

#[tokio::main]
async fn main() {
    let settings = Settings::from_env();
    if let Err(err) = settings.ensure_production_readiness() {
        eprintln!("{err}");
        std::process::exit(1);
    }
    let pool = match db::connect(&settings.database_path).await {
        Ok(pool) => pool,
        Err(err) => {
            eprintln!("Không mở được SQLite {}: {err}", settings.database_path);
            std::process::exit(1);
        }
    };
    if let Err(err) = db::ensure_schema(&pool).await {
        eprintln!("Không tạo được schema: {err}");
        std::process::exit(1);
    }

    let port = settings.port;
    let http = reqwest::Client::new();
    let app = build_router(AppState {
        pool,
        settings,
        http,
        limiter: std::sync::Arc::new(quanh_ta_backend::rate_limit::RateLimiter::default()),
        oauth_states: std::sync::Arc::new(std::sync::Mutex::new(std::collections::HashMap::new())),
        oauth_exchanges: std::sync::Arc::new(std::sync::Mutex::new(std::collections::HashMap::new())),
    });
    let addr = format!("0.0.0.0:{port}");
    match tokio::net::TcpListener::bind(&addr).await {
        Ok(listener) => {
            eprintln!("Quanh Ta backend (Rust/Axum) đang nghe tại http://{addr}");
            if let Err(err) = axum::serve(
                listener,
                app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
            .await
            {
                eprintln!("Lỗi server: {err}");
                std::process::exit(1);
            }
        }
        Err(err) => {
            eprintln!("Không bind được {addr}: {err}");
            std::process::exit(1);
        }
    }
}
