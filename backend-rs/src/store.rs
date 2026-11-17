//! Truy vấn SQL — compile-time checked bằng sqlx macro (kiểm tra cú pháp + kiểu
//! với schema của data/spends.db lúc build, qua DATABASE_URL trong .env).
//! Query động (WHERE thay đổi) dùng runtime query vẫn parameterized.

use sqlx::Pool;
use sqlx::Sqlite;

use crate::error::{AppError, RequestId};

pub type Db = Pool<Sqlite>;

pub(crate) fn internal_debug(rid: &RequestId, err: impl std::fmt::Debug) -> AppError {
    internal(rid, err)
}

fn internal(rid: &RequestId, err: impl std::fmt::Debug) -> AppError {
    eprintln!("[quanh-ta] db_error request_id={} {err:?}", rid.0);
    AppError::new(
        "INTERNAL_ERROR",
        "Có lỗi xảy ra. Vui lòng thử lại.",
        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
    )
    .with_request_id(rid)
}

// ---- Users ----

#[derive(Debug, sqlx::FromRow)]
pub struct UserRow {
    pub id: String,
    pub username: String,
    pub password_hash: String,
    pub created_at: String,
    pub verified: bool,
    pub email: String,
}

pub async fn get_user_by_id(db: &Db, rid: &RequestId, id: &str) -> Result<Option<UserRow>, AppError> {
    sqlx::query_as!(
        UserRow,
        r#"SELECT id, username, password_hash,
                  created_at AS "created_at: String", verified, email
           FROM users WHERE id = $1"#,
        id
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

pub async fn get_user_by_username(
    db: &Db,
    rid: &RequestId,
    username: &str,
) -> Result<Option<UserRow>, AppError> {
    sqlx::query_as!(
        UserRow,
        r#"SELECT id, username, password_hash,
                  created_at AS "created_at: String", verified, email
           FROM users WHERE username = $1"#,
        username
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

/// get_by_email của Python: WHERE lower(email) = <strip + casefold>.
/// casefold của Rust là to_lowercase — đủ cho email thực tế (khác biệt ß/Đ hiếm).

/// Tìm user theo username "dễ tha": exact → lower → bỏ dấu (normalize_name).
/// Dùng cho login + dev-reset để gõ "khanh" vẫn ra "Khánh". Register vẫn
/// so khớp exact (không đổi contract tạo tài khoản).
pub async fn get_user_by_username_tolerant(
    db: &Db,
    rid: &RequestId,
    username: &str,
) -> Result<Option<UserRow>, AppError> {
    if let Some(u) = get_user_by_username(db, rid, username).await? {
        return Ok(Some(u));
    }
    // lower match (SQL)
    let lowered = username.trim().to_lowercase();
    let row = sqlx::query_as::<_, UserRow>(
        "SELECT id, username, password_hash, CAST(created_at AS TEXT) AS created_at, verified, email \
         FROM users WHERE lower(username) = $1",
    )
    .bind(&lowered)
    .fetch_optional(db)
    .await;
    if let Ok(Some(u)) = row {
        return Ok(Some(u));
    }
    // bỏ dấu: quét danh sách user (app cá nhân, số lượng rất nhỏ)
    let folded = crate::geo::normalize_name(username);
    if folded.is_empty() {
        return Ok(None);
    }
    let all = sqlx::query_as::<_, UserRow>(
        "SELECT id, username, password_hash, CAST(created_at AS TEXT) AS created_at, verified, email FROM users",
    )
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))?;
    Ok(all
        .into_iter()
        .find(|u| crate::geo::normalize_name(&u.username) == folded))
}

pub async fn get_user_by_email_ci(
    db: &Db,
    rid: &RequestId,
    email_normalized: &str,
) -> Result<Option<UserRow>, AppError> {
    sqlx::query_as!(
        UserRow,
        r#"SELECT id, username, password_hash,
                  created_at AS "created_at: String", verified, email
           FROM users WHERE lower(email) = $1"#,
        email_normalized
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

pub async fn insert_user(db: &Db, rid: &RequestId, user: &UserRow) -> Result<(), AppError> {
    sqlx::query!(
        r#"INSERT INTO users (id, username, password_hash, created_at, verified, email)
           VALUES ($1, $2, $3, $4, $5, $6)"#,
        user.id,
        user.username,
        user.password_hash,
        user.created_at,
        user.verified,
        user.email,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

pub async fn set_verified(db: &Db, rid: &RequestId, id: &str, verified: bool) -> Result<(), AppError> {
    sqlx::query!("UPDATE users SET verified = $1 WHERE id = $2", verified, id)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

// ---- Refresh tokens ----

pub async fn store_refresh_token(
    db: &Db,
    rid: &RequestId,
    jti: &str,
    user_id: &str,
    expires_at: &str,
) -> Result<(), AppError> {
    let token_id = crate::security::new_id();
    let created_at = crate::timefmt::now_storage();
    sqlx::query!(
        r#"INSERT INTO refresh_tokens (id, user_id, jti, expires_at, revoked, created_at)
           VALUES ($1, $2, $3, $4, $5, $6)"#,
        token_id,
        user_id,
        jti,
        expires_at,
        false,
        created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

// ---- Places (chỉ cần name/category cho payload spend) ----

#[derive(Debug)]
pub struct PlaceInfo {
    pub name: String,
    pub category: String,
}

pub async fn get_place_info(
    db: &Db,
    rid: &RequestId,
    place_id: &str,
) -> Result<Option<PlaceInfo>, AppError> {
    sqlx::query_as!(PlaceInfo, "SELECT name, category FROM places WHERE id = $1", place_id)
        .fetch_optional(db)
        .await
        .map_err(|e| internal(rid, e))
}

// ---- Spends ----

#[derive(Debug)]
pub struct SpendInsert {
    pub id: String,
    pub user_id: String,
    pub place_id: String,
    pub amount: i64,
    pub party_size: i64,
    pub occurred_at: String,
    pub note: String,
    pub created_at: String,
}

pub async fn insert_spend(db: &Db, rid: &RequestId, s: &SpendInsert) -> Result<(), AppError> {
    sqlx::query!(
        r#"INSERT INTO spends (id, user_id, place_id, amount, party_size, occurred_at, note, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"#,
        s.id,
        s.user_id,
        s.place_id,
        s.amount,
        s.party_size,
        s.occurred_at,
        s.note,
        s.created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

#[derive(Debug, sqlx::FromRow)]
pub struct SpendListRow {
    pub id: String,
    pub place_id: String,
    pub place_name: Option<String>,
    pub category: Option<String>,
    pub amount: i64,
    pub party_size: i64,
    pub note: String,
    pub occurred_at: String,
    pub receipt_photo: Option<String>,
}

/// list(user_id, start, end) — ORDER BY occurred_at DESC như Python.
/// WHERE động nên dùng runtime query (vẫn parameterized).
pub async fn list_spends(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    start: Option<&str>,
    end: Option<&str>,
) -> Result<Vec<SpendListRow>, AppError> {
    let mut sql = String::from(
        "SELECT s.id, s.place_id, p.name AS place_name, p.category, \
         s.amount, s.party_size, s.note, CAST(s.occurred_at AS TEXT) AS occurred_at, \
         s.receipt_photo \
         FROM spends s LEFT JOIN places p ON p.id = s.place_id WHERE s.user_id = $1",
    );
    if start.is_some() {
        sql.push_str(" AND s.occurred_at >= $2");
    }
    if end.is_some() {
        sql.push_str(&format!(" AND s.occurred_at <= ${}", if start.is_some() { 3 } else { 2 }));
    }
    sql.push_str(" ORDER BY s.occurred_at DESC");
    let mut q = sqlx::query_as::<_, SpendListRow>(&sql).bind(user_id);
    if let Some(s) = start {
        q = q.bind(s);
    }
    if let Some(e) = end {
        q = q.bind(e);
    }
    q.fetch_all(db).await.map_err(|e| internal(rid, e))
}

pub async fn delete_spend(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    spend_id: &str,
) -> Result<(), AppError> {
    sqlx::query!("DELETE FROM spends WHERE id = $1 AND user_id = $2", spend_id, user_id)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

/// Lưu đường dẫn ảnh biên lai cho giao dịch (chỉ owner).
pub async fn set_receipt_photo(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    spend_id: &str,
    path: &str,
) -> Result<(), AppError> {
    sqlx::query!(
        "UPDATE spends SET receipt_photo = $1 WHERE id = $2 AND user_id = $3",
        path,
        spend_id,
        user_id
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

/// Đường dẫn ảnh biên lai đã lưu (None nếu chưa có).
pub async fn get_receipt_photo(
    db: &Db,
    rid: &RequestId,
    spend_id: &str,
) -> Result<Option<String>, AppError> {
    sqlx::query!("SELECT receipt_photo FROM spends WHERE id = $1", spend_id)
        .fetch_optional(db)
        .await
        .map(|row| row.and_then(|r| r.receipt_photo))
        .map_err(|e| internal(rid, e))
}

/// user_id của spend — dùng kiểm tra quyền trước khi xoá.
pub async fn get_spend_owner(
    db: &Db,
    rid: &RequestId,
    spend_id: &str,
) -> Result<Option<String>, AppError> {
    sqlx::query!("SELECT user_id FROM spends WHERE id = $1", spend_id)
        .fetch_optional(db)
        .await
        .map(|row| row.map(|r| r.user_id))
        .map_err(|e| internal(rid, e))
}

// ---- Spend drafts ----

#[derive(Debug)]
pub struct DraftInsert {
    pub id: String,
    pub user_id: String,
    pub amount: i64,
    pub party_size: i64,
    pub merchant_name: String,
    pub bank_bin: String,
    pub account: String,
    pub place_id: Option<String>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub note: String,
    pub created_at: String,
}

pub async fn insert_draft(db: &Db, rid: &RequestId, d: &DraftInsert) -> Result<(), AppError> {
    sqlx::query!(
        r#"INSERT INTO spend_drafts
           (id, user_id, amount, party_size, merchant_name, bank_bin, account,
            place_id, lat, lon, note, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"#,
        d.id,
        d.user_id,
        d.amount,
        d.party_size,
        d.merchant_name,
        d.bank_bin,
        d.account,
        d.place_id,
        d.lat,
        d.lon,
        d.note,
        d.created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

#[derive(Debug, sqlx::FromRow)]
pub struct DraftRow {
    pub id: String,
    pub user_id: String,
    pub amount: i64,
    pub party_size: i64,
    pub merchant_name: String,
    pub bank_bin: String,
    pub account: String,
    pub place_id: Option<String>,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub note: String,
    pub created_at: String,
}

pub async fn list_drafts(db: &Db, rid: &RequestId, user_id: &str) -> Result<Vec<DraftRow>, AppError> {
    sqlx::query_as::<_, DraftRow>(
        "SELECT id, user_id, amount, party_size, merchant_name, bank_bin, account, \
         place_id, lat, lon, note, CAST(created_at AS TEXT) AS created_at \
         FROM spend_drafts WHERE user_id = ? ORDER BY created_at DESC",
    )
    .bind(user_id)
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))
}

/// get + quyền sở hữu: None hoặc user khác → False (Python trả 404 như nhau).
pub async fn delete_draft_owned(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    draft_id: &str,
) -> Result<bool, AppError> {
    let row = sqlx::query!(
        "SELECT user_id FROM spend_drafts WHERE id = $1",
        draft_id
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))?;
    match row {
        Some(r) if r.user_id == user_id => {
            sqlx::query!("DELETE FROM spend_drafts WHERE id = $1", draft_id)
                .execute(db)
                .await
                .map(|_| ())
                .map_err(|e| internal(rid, e))?;
            Ok(true)
        }
        _ => Ok(false),
    }
}

/// Draft đầy đủ cho confirm — macro check type với schema thật.
pub async fn get_draft(db: &Db, rid: &RequestId, draft_id: &str) -> Result<Option<DraftRow>, AppError> {
    sqlx::query_as::<_, DraftRow>(
        "SELECT id, user_id, amount, party_size, merchant_name, bank_bin, account, \
         place_id, lat, lon, note, CAST(created_at AS TEXT) AS created_at \
         FROM spend_drafts WHERE id = ?",
    )
    .bind(draft_id)
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

// ---- Places ----

#[derive(Debug, Clone, sqlx::FromRow)]
pub struct PlaceRow {
    pub id: String,
    pub name: String,
    pub normalized_name: String,
    pub category: String,
    pub address: String,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub created_by: Option<String>,
    pub created_at: String,
}

pub struct PlaceInsert {
    pub id: String,
    pub name: String,
    pub normalized_name: String,
    pub category: String,
    pub address: String,
    pub lat: Option<f64>,
    pub lon: Option<f64>,
    pub created_by: Option<String>,
    pub created_at: String,
}

pub async fn insert_place(db: &Db, rid: &RequestId, p: &PlaceInsert) -> Result<(), AppError> {
    sqlx::query!(
        r#"INSERT INTO places (id, name, normalized_name, category, address, lat, lon, created_by, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)"#,
        p.id,
        p.name,
        p.normalized_name,
        p.category,
        p.address,
        p.lat,
        p.lon,
        p.created_by,
        p.created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

/// Place đầy đủ theo id.
pub async fn get_place(db: &Db, rid: &RequestId, place_id: &str) -> Result<Option<PlaceRow>, AppError> {
    sqlx::query_as::<_, PlaceRow>(
        "SELECT id, name, normalized_name, category, address, lat, lon, created_by, \
         CAST(created_at AS TEXT) AS created_at FROM places WHERE id = ?",
    )
    .bind(place_id)
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

/// search(q, category) của Python: LIKE (SQLite ASCII case-insensitive),
/// ORDER BY name. Q đã được normalize_name ở tầng route.
pub async fn search_places(
    db: &Db,
    rid: &RequestId,
    q: &str,
    category: &str,
) -> Result<Vec<PlaceRow>, AppError> {
    let mut sql = String::from(
        "SELECT id, name, normalized_name, category, address, lat, lon, created_by, \
         CAST(created_at AS TEXT) AS created_at FROM places WHERE 1=1",
    );
    let mut binds: Vec<&str> = Vec::new();
    if !q.is_empty() {
        sql.push_str(" AND normalized_name LIKE '%' || ? || '%'");
        binds.push(q);
    }
    if !category.is_empty() {
        sql.push_str(" AND category = ?");
        binds.push(category);
    }
    sql.push_str(" ORDER BY name");
    let mut stmt = sqlx::query_as::<_, PlaceRow>(&sql);
    for b in binds {
        stmt = stmt.bind(b);
    }
    stmt.fetch_all(db).await.map_err(|e| internal(rid, e))
}

/// Ứ viên find_match: các place trùng normalized_name — quyền chọn cùng-quán
/// (≤ SAME_PLACE_RADIUS_KM hoặc cùng không có toạ độ) do route quyết định.
pub async fn find_match_candidates(
    db: &Db,
    rid: &RequestId,
    normalized_name: &str,
) -> Result<Vec<PlaceRow>, AppError> {
    sqlx::query_as::<_, PlaceRow>(
        "SELECT id, name, normalized_name, category, address, lat, lon, created_by, \
         CAST(created_at AS TEXT) AS created_at FROM places WHERE normalized_name = ?",
    )
    .bind(normalized_name)
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))
}

// ---- Thống kê chi tiêu per place (spend_repo.per_place của Python) ----

#[derive(Debug, sqlx::FromRow)]
pub struct SpendStatRow {
    pub place_id: String,
    pub amount: i64,
    pub party_size: i64,
    pub occurred_at: String,
}

pub async fn list_spend_stats(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
) -> Result<Vec<SpendStatRow>, AppError> {
    sqlx::query_as::<_, SpendStatRow>(
        "SELECT place_id, amount, party_size, CAST(occurred_at AS TEXT) AS occurred_at \
         FROM spends WHERE user_id = ?",
    )
    .bind(user_id)
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))
}

/// spend_repo.category_visits: số lần ghi chi tiêu theo loại địa điểm
/// (chỉ đếm spend có place tồn tại — JOIN bảo đảm điều đó).
#[derive(Debug, sqlx::FromRow)]
pub struct CategoryCount {
    pub category: String,
    pub visits: i64,
}

pub async fn category_visits(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
) -> Result<Vec<CategoryCount>, AppError> {
    sqlx::query_as::<_, CategoryCount>(
        "SELECT p.category AS category, COUNT(*) AS visits \
         FROM spends s JOIN places p ON p.id = s.place_id WHERE s.user_id = ? \
         GROUP BY p.category",
    )
    .bind(user_id)
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))
}

// ---- Email verification / password reset / refresh tokens (auth_ext) ----

#[derive(Debug)]
pub struct TokenRow {
    pub id: String,
    pub user_id: String,
    pub expires_at: String,
    pub used: bool,
}

pub async fn get_email_verification(
    db: &Db,
    rid: &RequestId,
    token: &str,
) -> Result<Option<TokenRow>, AppError> {
    sqlx::query_as!(
        TokenRow,
        "SELECT id, user_id, expires_at, used FROM email_verifications WHERE token = $1",
        token
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

pub async fn create_email_verification(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    token: &str,
    expires_at: &str,
) -> Result<(), AppError> {
    let row_id = crate::security::new_id();
    let created_at = crate::timefmt::now_storage();
    sqlx::query!(
        "INSERT INTO email_verifications (id, user_id, token, expires_at, used, created_at) \
         VALUES ($1, $2, $3, $4, $5, $6)",
        row_id,
        user_id,
        token,
        expires_at,
        false,
        created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

pub async fn mark_email_verification_used(
    db: &Db,
    rid: &RequestId,
    id: &str,
) -> Result<(), AppError> {
    sqlx::query!("UPDATE email_verifications SET used = $1 WHERE id = $2", true, id)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

pub async fn get_password_reset(
    db: &Db,
    rid: &RequestId,
    token: &str,
) -> Result<Option<TokenRow>, AppError> {
    sqlx::query_as!(
        TokenRow,
        "SELECT id, user_id, expires_at, used FROM password_resets WHERE token = $1",
        token
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

/// Có mã reset còn hạn/chưa dùng của user không — KHÔNG trả nội dung mã.
pub async fn has_active_reset(db: &Db, rid: &RequestId, user_id: &str) -> Result<bool, AppError> {
    let rows = sqlx::query_as!(
        TokenRow,
        "SELECT id, user_id, expires_at, used FROM password_resets WHERE user_id = $1 AND used = 0",
        user_id
    )
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))?;
    let now = chrono::Utc::now().timestamp();
    Ok(rows.iter().any(|r| {
        crate::timefmt::parse_expiry(&r.expires_at)
            .map(|dt| dt.timestamp() > now)
            .unwrap_or(false)
    }))
}

pub async fn create_password_reset(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    token: &str,
    expires_at: &str,
) -> Result<(), AppError> {
    let row_id = crate::security::new_id();
    let created_at = crate::timefmt::now_storage();
    sqlx::query!(
        "INSERT INTO password_resets (id, user_id, token, expires_at, used, created_at) \
         VALUES ($1, $2, $3, $4, $5, $6)",
        row_id,
        user_id,
        token,
        expires_at,
        false,
        created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

pub async fn mark_password_reset_used(
    db: &Db,
    rid: &RequestId,
    id: &str,
) -> Result<(), AppError> {
    sqlx::query!("UPDATE password_resets SET used = $1 WHERE id = $2", true, id)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

pub async fn update_password(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    password_hash: &str,
) -> Result<(), AppError> {
    sqlx::query!("UPDATE users SET password_hash = $1 WHERE id = $2", password_hash, user_id)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

/// is_refresh_token_valid: tồn tại, chưa revoked, expires_at còn hạn → user_id.
pub async fn valid_refresh_user(
    db: &Db,
    rid: &RequestId,
    jti: &str,
) -> Result<Option<String>, AppError> {
    let row = sqlx::query!(
        "SELECT user_id, revoked, expires_at FROM refresh_tokens WHERE jti = $1",
        jti
    )
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))?;
    let Some(row) = row else { return Ok(None) };
    if row.revoked {
        return Ok(None);
    }
    match crate::timefmt::parse_expiry(&row.expires_at) {
        Some(exp) if exp > chrono::Utc::now() => Ok(Some(row.user_id)),
        _ => Ok(None),
    }
}

pub async fn revoke_refresh_token(db: &Db, rid: &RequestId, jti: &str) -> Result<(), AppError> {
    sqlx::query!("UPDATE refresh_tokens SET revoked = $1 WHERE jti = $2", true, jti)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

/// reset-password: thu hồi toàn bộ refresh token của user.
pub async fn revoke_all_user_refresh_tokens(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
) -> Result<(), AppError> {
    sqlx::query!("UPDATE refresh_tokens SET revoked = $1 WHERE user_id = $2", true, user_id)
        .execute(db)
        .await
        .map(|_| ())
        .map_err(|e| internal(rid, e))
}

// ---- OAuth accounts (Google OAuth) ----

#[derive(Debug, Clone, sqlx::FromRow)]
pub struct OAuthAccountRow {
    pub id: String,
    pub user_id: String,
    pub email: String,
}

pub async fn get_oauth_account(
    db: &Db,
    rid: &RequestId,
    provider: &str,
    provider_account_id: &str,
) -> Result<Option<OAuthAccountRow>, AppError> {
    sqlx::query_as::<_, OAuthAccountRow>(
        "SELECT id, user_id, email FROM oauth_accounts \
         WHERE provider = ? AND provider_account_id = ?",
    )
    .bind(provider)
    .bind(provider_account_id)
    .fetch_optional(db)
    .await
    .map_err(|e| internal(rid, e))
}

pub async fn list_oauth_accounts(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
) -> Result<Vec<OAuthAccountRow>, AppError> {
    sqlx::query_as::<_, OAuthAccountRow>(
        "SELECT id, user_id, email FROM oauth_accounts WHERE user_id = ?",
    )
    .bind(user_id)
    .fetch_all(db)
    .await
    .map_err(|e| internal(rid, e))
}

/// link_oauth_account của Python — thêm định danh ngoài cho user.
pub async fn link_oauth_account(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    provider: &str,
    provider_account_id: &str,
    email: &str,
) -> Result<(), AppError> {
    let row_id = crate::security::new_id();
    let created_at = crate::timefmt::now_storage();
    sqlx::query!(
        "INSERT INTO oauth_accounts (id, user_id, provider, provider_account_id, email, created_at) \
         VALUES ($1, $2, $3, $4, $5, $6)",
        row_id,
        user_id,
        provider,
        provider_account_id,
        email,
        created_at,
    )
    .execute(db)
    .await
    .map(|_| ())
    .map_err(|e| internal(rid, e))
}

/// Trả false khi không có account của provider để xoá (unlink_oauth_account của Python).
pub async fn unlink_oauth_account(
    db: &Db,
    rid: &RequestId,
    user_id: &str,
    provider: &str,
) -> Result<bool, AppError> {
    let result = sqlx::query!(
        "DELETE FROM oauth_accounts WHERE user_id = $1 AND provider = $2",
        user_id,
        provider
    )
    .execute(db)
    .await
    .map_err(|e| internal(rid, e))?;
    Ok(result.rows_affected() > 0)
}
