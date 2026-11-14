//! Bảo mật: PBKDF2-sha256 + JWT HS256 — tương thích chéo với src/auth/security.py.
//!
//! - Hash mật khẩu: base64(salt(16) + dk(32)), 100_000 vòng — user tạo bằng Python
//!   đăng nhập được qua Rust và ngược lại.
//! - JWT: HS256, payload {"sub", "exp"} — do PyJWT tạo cũng decode được ở đây.

use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine;
use jsonwebtoken::{decode, encode, Algorithm, DecodingKey, EncodingKey, Header, Validation};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use uuid::Uuid;

const ITERATIONS: u32 = 100_000;
const SALT_LEN: usize = 16;
const DK_LEN: usize = 32; // sha256 output
pub const REFRESH_DAYS: i64 = 30;

// ---- Password hashing ----

pub fn hash_password(password: &str) -> String {
    let mut salt = [0u8; SALT_LEN];
    getrandom::getrandom(&mut salt).expect("hệ thống hết entropy");
    let mut dk = [0u8; DK_LEN];
    pbkdf2::pbkdf2_hmac::<Sha256>(password.as_bytes(), &salt, ITERATIONS, &mut dk);
    let mut raw = [0u8; SALT_LEN + DK_LEN];
    raw[..SALT_LEN].copy_from_slice(&salt);
    raw[SALT_LEN..].copy_from_slice(&dk);
    STANDARD.encode(raw)
}

pub fn verify_password(password: &str, stored: &str) -> bool {
    let Ok(raw) = STANDARD.decode(stored) else {
        return false;
    };
    if raw.len() < SALT_LEN + DK_LEN {
        return false;
    }
    let (salt, dk) = raw.split_at(SALT_LEN);
    let mut new = [0u8; DK_LEN];
    pbkdf2::pbkdf2_hmac::<Sha256>(password.as_bytes(), salt, ITERATIONS, &mut new);
    // so sánh constant-time như secrets.compare_digest
    bool::from(new.ct_eq(dk))
}

// ---- JWT (access) ----

#[derive(Serialize, Deserialize)]
struct AccessClaims {
    sub: String,
    exp: u64,
}

pub fn create_token(user_id: &str, days: i64, secret: &str) -> String {
    let exp = (chrono::Utc::now() + chrono::Duration::days(days)).timestamp() as u64;
    let claims = AccessClaims {
        sub: user_id.into(),
        exp,
    };
    encode(&Header::new(Algorithm::HS256), &claims, &EncodingKey::from_secret(secret.as_bytes()))
        .expect("HS256 encode không thể lỗi")
}

pub fn decode_token(token: &str, secret: &str) -> Option<String> {
    let mut validation = Validation::new(Algorithm::HS256);
    validation.validate_exp = true;
    let data = decode::<AccessClaims>(token, &DecodingKey::from_secret(secret.as_bytes()), &validation)
        .ok()?;
    Some(data.claims.sub)
}

// ---- Refresh token JWT ----

#[derive(Serialize, Deserialize)]
struct RefreshClaims {
    sub: String,
    jti: String,
    #[serde(rename = "type")]
    token_type: String,
    exp: u64,
}

pub fn create_refresh_token(user_id: &str, jti: &str, secret: &str) -> String {
    let exp = (chrono::Utc::now() + chrono::Duration::days(REFRESH_DAYS)).timestamp() as u64;
    let claims = RefreshClaims {
        sub: user_id.into(),
        jti: jti.into(),
        token_type: "refresh".into(),
        exp,
    };
    encode(&Header::new(Algorithm::HS256), &claims, &EncodingKey::from_secret(secret.as_bytes()))
        .expect("HS256 encode không thể lỗi")
}

/// Trả về (user_id, jti) — None nếu sai loại/sai chữ ký/hết hạn.
pub fn decode_refresh_token(token: &str, secret: &str) -> Option<(String, String)> {
    let mut validation = Validation::new(Algorithm::HS256);
    validation.validate_exp = true;
    let data =
        decode::<RefreshClaims>(token, &DecodingKey::from_secret(secret.as_bytes()), &validation)
            .ok()?;
    if data.claims.token_type != "refresh" {
        return None;
    }
    Some((data.claims.sub, data.claims.jti))
}

// ---- Sinh định danh ----

/// uuid4().hex[:16] của Python — 16 ký tự hex.
pub fn new_id() -> String {
    Uuid::new_v4().simple().to_string()[..16].to_string()
}

/// secrets.token_urlsafe(24) của Python — 24 byte random, base64url không padding (32 ký tự).
pub fn new_jti() -> String {
    let mut buf = [0u8; 24];
    getrandom::getrandom(&mut buf).expect("hệ thống hết entropy");
    URL_SAFE_NO_PAD.encode(buf)
}

/// secrets.token_urlsafe(32) của Python — email token + jti xoay vòng refresh
/// (auth_ext dùng 32 byte = 43 ký tự base64url).
pub fn token_urlsafe_32() -> String {
    let mut buf = [0u8; 32];
    getrandom::getrandom(&mut buf).expect("hệ thống hết entropy");
    URL_SAFE_NO_PAD.encode(buf)
}

/// uuid4().hex của Python — ĐẦY ĐỦ 32 ký tự hex (dùng cho jti của Google OAuth).
pub fn uuid_hex32() -> String {
    Uuid::new_v4().simple().to_string()
}

/// secrets.token_hex(3) của Python — 6 ký tự hex từ 3 byte random.
pub fn random_hex6() -> String {
    let mut buf = [0u8; 3];
    getrandom::getrandom(&mut buf).expect("hệ thống hết entropy");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// uuid4().hex[:12] — mặc định của RequestIdMiddleware khi client không gửi header.
pub fn new_request_id() -> String {
    Uuid::new_v4().simple().to_string()[..12].to_string()
}

/// SHA-256 hex — dùng cho hash token email/reset (auth_ext, port sau).
pub fn sha256_hex(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    let out = hasher.finalize();
    out.iter().map(|b| format!("{b:02x}")).collect()
}

// ---- Generic JWT helpers (Google OAuth state + id_token) ----

/// jwt.encode HS256 bất kỳ payload nào.
pub fn encode_hs256<T: Serialize>(claims: &T, secret: &str) -> String {
    encode(
        &Header::new(Algorithm::HS256),
        claims,
        &EncodingKey::from_secret(secret.as_bytes()),
    )
    .expect("HS256 encode không thể lỗi")
}

/// jwt.decode HS256 bất kỳ payload nào — None nếu sai/hết hạn.
pub fn decode_hs256<T: DeserializeOwned>(token: &str, secret: &str) -> Option<T> {
    let mut validation = Validation::new(Algorithm::HS256);
    validation.validate_exp = true;
    decode::<T>(token, &DecodingKey::from_secret(secret.as_bytes()), &validation)
        .ok()
        .map(|data| data.claims)
}

/// jwt.get_unverified_header của Python — đọc header không kiểm chứng.
pub fn jwt_header(token: &str) -> Option<Header> {
    jsonwebtoken::decode_header(token).ok()
}

/// jwt.decode RS256 id_token của Google: audience = client_id, issuers như
/// Python, exp mặc định validate. Trả claims thô (Value) cho caller shape.
pub fn decode_rs256_google(token: &str, jwk: &jsonwebtoken::jwk::Jwk, audience: &str) -> Option<Value> {
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_audience(&[audience]);
    validation.set_issuer(&["https://accounts.google.com", "accounts.google.com"]);
    let key = DecodingKey::from_jwk(jwk).ok()?;
    decode::<Value>(token, &key, &validation).ok().map(|data| data.claims)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Hash do Python sinh: hash_password('parity-secret-123') từ data/spends.db.
    const PY_HASH: &str =
        "fBaRb7F3AWhzCHuEkGBZhf1h7NVWjJTvD47DH6C9H3Ut454pNy6ukW/vnjxglWnI";
    const PY_PASSWORD: &str = "parity-secret-123";

    #[test]
    fn verifies_python_generated_hash() {
        assert!(verify_password(PY_PASSWORD, PY_HASH));
        assert!(!verify_password("sai-mat-khau", PY_HASH));
        assert!(!verify_password(PY_PASSWORD, "hash-rác"));
    }

    #[test]
    fn rust_hash_format_matches_python() {
        let hashed = hash_password(PY_PASSWORD);
        // base64 chuẩn, decode ra 48 byte = salt16 + dk32
        let raw = STANDARD.decode(&hashed).unwrap();
        assert_eq!(raw.len(), 48);
        assert!(verify_password(PY_PASSWORD, &hashed));
    }

    // Token do PyJWT sinh với exp 2099 — chứng minh tương thích chéo.
    const PY_TOKEN: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJwYXJpdHl1c2VyMTIzNDU2NzgiLCJleHAiOjQwNzA5MDg4MDB9.p-MkY_A4dsDWUH1kS1DqNcIe9r8EfNOl-IzQYzuKqCY";
    const PY_SECRET: &str = "test-secret-for-parity-only";

    #[test]
    fn decodes_pyjwt_generated_token() {
        let sub = decode_token(PY_TOKEN, PY_SECRET).unwrap();
        assert_eq!(sub, "parityuser12345678");
        assert!(decode_token(PY_TOKEN, "sai-secret").is_none());
        assert!(decode_token("token.rác.không-hợp-lệ", PY_SECRET).is_none());
    }

    #[test]
    fn rust_token_roundtrip() {
        let token = create_token("user123", 7, PY_SECRET);
        assert_eq!(decode_token(&token, PY_SECRET).unwrap(), "user123");
    }

    #[test]
    fn id_shapes_match_python() {
        let id = new_id();
        assert_eq!(id.len(), 16);
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
        let jti = new_jti();
        assert_eq!(jti.len(), 32); // token_urlsafe(24) → 32 ký tự base64url
        assert!(jti.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
        assert_eq!(new_request_id().len(), 12);
    }
}
