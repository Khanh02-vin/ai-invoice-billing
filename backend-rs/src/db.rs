//! SQLite pool + schema — đọc trực tiếp file DB hiện có (WAL), không migrate dữ liệu.
//!
//! Pragmas khớp Database.__init__ của Python: journal_mode=WAL, busy_timeout=5000,
//! foreign_keys=ON. ensure_schema() chạy CREATE TABLE/INDEX IF NOT EXISTS với DDL
//! lấy nguyên văn từ sqlite_master của data/spends.db (SQLModel sinh ra).

use std::path::Path;
use std::time::Duration;

use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePool, SqlitePoolOptions};

pub type Pool = SqlitePool;

pub async fn connect(path: &str) -> Result<Pool, sqlx::Error> {
    let options = SqliteConnectOptions::new()
        .filename(Path::new(path))
        .create_if_missing(true)
        .journal_mode(SqliteJournalMode::Wal)
        .busy_timeout(Duration::from_millis(5000))
        .foreign_keys(true);
    SqlitePoolOptions::new()
        .max_connections(5)
        .connect_with(options)
        .await
}

/// Readiness probe: pool mở và DB trả lời được.
pub async fn ping(pool: &Pool) -> bool {
    sqlx::query("SELECT 1").execute(pool).await.is_ok()
}

const TABLE_DDL: [&str; 8] = [
    r#"CREATE TABLE IF NOT EXISTS users (
	id VARCHAR NOT NULL,
	username VARCHAR(64) NOT NULL,
	password_hash VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	verified BOOLEAN NOT NULL,
	email VARCHAR(254) NOT NULL,
	PRIMARY KEY (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS places (
	id VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	normalized_name VARCHAR NOT NULL,
	category VARCHAR NOT NULL,
	address VARCHAR NOT NULL,
	lat FLOAT,
	lon FLOAT,
	created_by VARCHAR,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS oauth_accounts (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	provider VARCHAR NOT NULL,
	provider_account_id VARCHAR NOT NULL,
	email VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (provider, provider_account_id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS email_verifications (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	token VARCHAR NOT NULL,
	expires_at VARCHAR NOT NULL,
	used BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS password_resets (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	token VARCHAR NOT NULL,
	expires_at VARCHAR NOT NULL,
	used BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS refresh_tokens (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	jti VARCHAR NOT NULL,
	expires_at VARCHAR NOT NULL,
	revoked BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS spends (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	place_id VARCHAR NOT NULL,
	amount INTEGER NOT NULL,
	party_size INTEGER NOT NULL,
	occurred_at DATETIME NOT NULL,
	note VARCHAR NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(place_id) REFERENCES places (id)
)"#,
    r#"CREATE TABLE IF NOT EXISTS spend_drafts (
	id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	amount INTEGER NOT NULL,
	party_size INTEGER NOT NULL,
	merchant_name VARCHAR(120) NOT NULL,
	bank_bin VARCHAR(6) NOT NULL,
	account VARCHAR(32) NOT NULL,
	place_id VARCHAR,
	lat FLOAT,
	lon FLOAT,
	note VARCHAR(200) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)"#,
];

const INDEX_DDL: [&str; 19] = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_email_verifications_token ON email_verifications (token)",
    "CREATE INDEX IF NOT EXISTS ix_email_verifications_user_id ON email_verifications (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_oauth_accounts_provider ON oauth_accounts (provider)",
    "CREATE INDEX IF NOT EXISTS ix_oauth_accounts_provider_account_id ON oauth_accounts (provider_account_id)",
    "CREATE INDEX IF NOT EXISTS ix_oauth_accounts_user_id ON oauth_accounts (user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_password_resets_token ON password_resets (token)",
    "CREATE INDEX IF NOT EXISTS ix_password_resets_user_id ON password_resets (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_places_category ON places (category)",
    "CREATE INDEX IF NOT EXISTS ix_places_created_by ON places (created_by)",
    "CREATE INDEX IF NOT EXISTS ix_places_normalized_name ON places (normalized_name)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_refresh_tokens_jti ON refresh_tokens (jti)",
    "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_spend_drafts_created_at ON spend_drafts (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_spend_drafts_place_id ON spend_drafts (place_id)",
    "CREATE INDEX IF NOT EXISTS ix_spend_drafts_user_id ON spend_drafts (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_spends_occurred_at ON spends (occurred_at)",
    "CREATE INDEX IF NOT EXISTS ix_spends_place_id ON spends (place_id)",
    "CREATE INDEX IF NOT EXISTS ix_spends_user_id ON spends (user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)",
];

/// Tạo bảng/index còn thiếu — idempotent, không đụng dữ liệu có sẵn.
pub async fn ensure_schema(pool: &Pool) -> Result<(), sqlx::Error> {
    let mut tx = pool.begin().await?;
    for ddl in TABLE_DDL {
        sqlx::query(ddl).execute(&mut *tx).await?;
    }
    for ddl in INDEX_DDL {
        sqlx::query(ddl).execute(&mut *tx).await?;
    }
    tx.commit().await
}
