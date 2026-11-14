//! Backend Axum cho Quanh Ta — drop-in thay thế FastAPI, đọc trực tiếp SQLite WAL
//! hiện có (không migrate dữ liệu). Xem README.md trong crate này.

pub mod db;
pub mod discovery;
pub mod error;
pub mod extract;
pub mod geo;
pub mod mail;
pub mod osm;
pub mod qr;
pub mod rate_limit;
pub mod routes;
pub mod security;
pub mod settings;
pub mod store;
pub mod timefmt;
pub mod validation;
