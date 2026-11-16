-- Schema DDL nguyên văn từ sqlite_master (SQLModel sinh) — nguồn cho
-- fixtures/schema.db dùng lúc BUILD docker (sqlx macro kiểm tra SQL lúc compile).
-- PHẢI GIỦ ĐỒNG BỘ với TABLE_DDL/INDEX_DDL trong src/db.rs (runtime ensure_schema)
-- + cột receipt_photo được db.rs migrate_columns ALTER thêm cho DB cũ (không xuất
-- hiện trong sqlite_master gốc — cột này duyệt tay tại đây).
-- Tái tạo: sqlite3 data/spends.db "SELECT sql || ';' FROM sqlite_master WHERE (type='table' AND name IN ('users','oauth_accounts','email_verifications','password_resets','refresh_tokens','places','spends','spend_drafts')) OR (type='index' AND sql IS NOT NULL) ORDER BY type DESC, name;" > fixtures/schema.sql
CREATE TABLE email_verifications (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	token VARCHAR NOT NULL, 
	expires_at VARCHAR NOT NULL, 
	used BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE oauth_accounts (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	provider_account_id VARCHAR NOT NULL, 
	email VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (provider, provider_account_id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE password_resets (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	token VARCHAR NOT NULL, 
	expires_at VARCHAR NOT NULL, 
	used BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE places (
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
);
CREATE TABLE refresh_tokens (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	jti VARCHAR NOT NULL, 
	expires_at VARCHAR NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE spend_drafts (
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
);
CREATE TABLE spends (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	place_id VARCHAR NOT NULL, 
	amount INTEGER NOT NULL, 
	party_size INTEGER NOT NULL, 
	occurred_at DATETIME NOT NULL, 
	note VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	receipt_photo VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(place_id) REFERENCES places (id)
);
CREATE TABLE users (
	id VARCHAR NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	verified BOOLEAN NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_email_verifications_token ON email_verifications (token);
CREATE INDEX ix_email_verifications_user_id ON email_verifications (user_id);
CREATE INDEX ix_oauth_accounts_provider ON oauth_accounts (provider);
CREATE INDEX ix_oauth_accounts_provider_account_id ON oauth_accounts (provider_account_id);
CREATE INDEX ix_oauth_accounts_user_id ON oauth_accounts (user_id);
CREATE UNIQUE INDEX ix_password_resets_token ON password_resets (token);
CREATE INDEX ix_password_resets_user_id ON password_resets (user_id);
CREATE INDEX ix_places_category ON places (category);
CREATE INDEX ix_places_created_by ON places (created_by);
CREATE INDEX ix_places_normalized_name ON places (normalized_name);
CREATE UNIQUE INDEX ix_refresh_tokens_jti ON refresh_tokens (jti);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);
CREATE INDEX ix_spend_drafts_created_at ON spend_drafts (created_at);
CREATE INDEX ix_spend_drafts_place_id ON spend_drafts (place_id);
CREATE INDEX ix_spend_drafts_user_id ON spend_drafts (user_id);
CREATE INDEX ix_spends_occurred_at ON spends (occurred_at);
CREATE INDEX ix_spends_place_id ON spends (place_id);
CREATE INDEX ix_spends_user_id ON spends (user_id);
CREATE UNIQUE INDEX ix_users_username ON users (username);
