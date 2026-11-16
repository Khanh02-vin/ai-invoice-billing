// Cấu hình từ biến môi trường — mirror của src/config.py.
//
// Không dùng default JWT secret ở production; fail-fast như Python.

use std::env;

pub const DEFAULT_DEV_SECRET: &str = "dev-secret-change-me-please-32bytes-min";
const DEFAULT_DEV_ORIGINS: [&str; 2] = ["http://localhost:5173", "http://127.0.0.1:5173"];

fn bool_env(name: &str, default: bool) -> bool {
    match env::var(name) {
        Ok(raw) => matches!(
            raw.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
        Err(_) => default,
    }
}

fn list_env(name: &str, default: &[&str]) -> Vec<String> {
    match env::var(name) {
        Ok(raw) if !raw.is_empty() => raw
            .split(',')
            .map(|s| s.trim().to_owned())
            .filter(|s| !s.is_empty())
            .collect(),
        _ => default.iter().map(|s| s.to_string()).collect(),
    }
}

#[derive(Clone, Debug)]
pub struct Settings {
    pub environment: String,
    pub jwt_secret: String,
    pub jwt_expiry_days: i64,
    pub open_registration: bool,
    pub auto_verify_email: bool,
    pub cors_origins: Vec<String>,
    pub database_path: String,
    pub port: u16,
    // Email transport (SMTP) — như config.py
    pub smtp_host: String,
    pub smtp_port: u16,
    pub smtp_user: String,
    pub smtp_pass: String,
    pub email_from: String,
    pub app_url: String,
    // Rate limit policies "<max>:<window>" — như config.py
    pub rate_limit_auth_login: String,
    pub rate_limit_auth_register: String,
    pub rate_limit_auth_resend: String,
    // Google OAuth
    pub google_oauth_client_id: String,
    pub google_oauth_client_secret: String,
    pub google_oauth_redirect_uri: String,
    // Opt-in tường minh: bật /auth/dev-reset-password (đặt lại mật khẩu không
    // cần email). Production KHÔNG bao giờ nên đặt biến này.
    pub dev_password_reset: bool,
}

impl Settings {
    pub fn from_env() -> Self {
        let jwt_expiry_days = env::var("JWT_EXPIRY_DAYS")
            .ok()
            .and_then(|v| v.trim().parse().ok())
            .unwrap_or(7);
        Self {
            environment: env::var("APP_ENV").unwrap_or_else(|_| "development".into()),
            jwt_secret: env::var("JWT_SECRET").unwrap_or_else(|_| DEFAULT_DEV_SECRET.into()),
            jwt_expiry_days,
            open_registration: bool_env("OPEN_REGISTRATION", false),
            auto_verify_email: bool_env("AUTO_VERIFY_EMAIL", false),
            cors_origins: list_env("CORS_ORIGINS", &DEFAULT_DEV_ORIGINS),
            database_path: env::var("DATABASE_PATH").unwrap_or_else(|_| "data/spends.db".into()),
            port: env::var("PORT")
                .ok()
                .and_then(|v| v.trim().parse().ok())
                .unwrap_or(8000),
            smtp_host: env::var("EMAIL_SMTP_HOST").unwrap_or_default(),
            smtp_port: env::var("EMAIL_SMTP_PORT")
                .ok()
                .and_then(|v| v.trim().parse().ok())
                .unwrap_or(0),
            smtp_user: env::var("EMAIL_SMTP_USER").unwrap_or_default(),
            smtp_pass: env::var("EMAIL_SMTP_PASS").unwrap_or_default(),
            email_from: env::var("EMAIL_FROM").unwrap_or_default(),
            app_url: env::var("APP_URL").unwrap_or_default(),
            rate_limit_auth_login: env::var("RL_AUTH_LOGIN").unwrap_or_else(|_| "5:60".into()),
            rate_limit_auth_register: env::var("RL_AUTH_REGISTER")
                .unwrap_or_else(|_| "3:60".into()),
            rate_limit_auth_resend: env::var("RL_AUTH_RESEND").unwrap_or_else(|_| "3:300".into()),
            google_oauth_client_id: env::var("GOOGLE_CLIENT_ID").unwrap_or_default(),
            google_oauth_client_secret: env::var("GOOGLE_CLIENT_SECRET").unwrap_or_default(),
            google_oauth_redirect_uri: env::var("GOOGLE_REDIRECT_URI").unwrap_or_default(),
            dev_password_reset: bool_env("ALLOW_DEV_PASSWORD_RESET", false),
        }
    }

    /// google_oauth_enabled của Python: đủ cả 3 giá trị mới bật.
    pub fn google_oauth_enabled(&self) -> bool {
        !self.google_oauth_client_id.is_empty()
            && !self.google_oauth_client_secret.is_empty()
            && !self.google_oauth_redirect_uri.is_empty()
    }

    /// Email transport bật khi SMTP host + port đã cấu hình — như Python.
    pub fn email_enabled(&self) -> bool {
        !self.smtp_host.is_empty() && self.smtp_port > 0
    }

    /// rate_limit_policy(name) của Python: "5:60" → (5, 60); hỏng → (10, 60).
    pub fn rate_limit_policy(&self, name: &str) -> (i64, i64) {
        let raw = match name {
            "rate_limit_auth_login" => &self.rate_limit_auth_login,
            "rate_limit_auth_register" => &self.rate_limit_auth_register,
            "rate_limit_auth_resend" => &self.rate_limit_auth_resend,
            _ => "10:60",
        };
        match raw.split_once(':') {
            Some((max, window)) => match (max.trim().parse(), window.trim().parse()) {
                (Ok(m), Ok(w)) => (m, w),
                _ => (10, 60),
            },
            None => (10, 60),
        }
    }

    pub fn is_production(&self) -> bool {
        self.environment.to_lowercase() == "production"
    }

    /// Fail-fast như ensure_production_readiness() của Python.
    pub fn ensure_production_readiness(&self) -> Result<(), String> {
        if !self.is_production() {
            return Ok(());
        }
        if self.jwt_secret.is_empty() || self.jwt_secret == DEFAULT_DEV_SECRET {
            return Err(
                "Production yêu cầu JWT_SECRET riêng (không được để mặc định). \
                 Hãy đặt biến môi trường JWT_SECRET."
                    .into(),
            );
        }
        if self.cors_origins.iter().any(|o| o == "*") {
            return Err(
                "Production không cho phép CORS wildcard. Hãy đặt CORS_ORIGINS danh sách trắng cụ thể."
                    .into(),
            );
        }
        Ok(())
    }
}
