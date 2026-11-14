//! Định dạng thời gian khớp byte với SQLAlchemy SQLite DATETIME và isoformat().
//!
//! - Lưu DB: `YYYY-MM-DD HH:MM:SS.ffffff` (dấu cách, không timezone, 6 chữ số micro).
//! - JSON trả về: `isoformat()` của Python — `T` ngăn cách, bỏ micro nếu == 0.

use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, Utc};

/// "Bây giờ" theo UTC wall-time — tương đương datetime.now(UTC) trước khi SQLAlchemy bỏ tzinfo.
pub fn now_storage() -> String {
    let now = chrono::Utc::now().naive_utc();
    format_storage(now)
}

/// NaiveDateTime → chuỗi lưu DB `YYYY-MM-DD HH:MM:SS.ffffff`.
pub fn format_storage(dt: NaiveDateTime) -> String {
    dt.format("%Y-%m-%d %H:%M:%S%.6f").to_string()
}

/// NaiveDateTime → isoformat() của Python cho JSON response.
///
/// SQLModel đọc DATETIME từ SQLite về **luôn gắn tzinfo=UTC** (probe đã xác nhận
/// kể cả với dòng lưu naive), nên response luôn kết thúc bằng "+00:00".
pub fn py_isoformat(dt: NaiveDateTime) -> String {
    let base = if dt.and_utc().timestamp_subsec_micros() == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    };
    format!("{base}+00:00")
}

/// Parse datetime do client gửi (Pydantic lax) → chuỗi lưu DB.
///
/// SQLAlchemy SQLite DATETIME bind: datetime **có offset → quy đổi về UTC** trước
/// khi lưu; datetime naive → giữ nguyên giờ wall. Rust làm y hệt.
pub fn parse_client_datetime(s: &str) -> Option<String> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    // RFC3339: có offset (Z / +hh:mm) → quy đổi UTC như SQLAlchemy bind
    if let Ok(dt) = DateTime::<FixedOffset>::parse_from_rfc3339(s) {
        return Some(format_storage(dt.with_timezone(&chrono::Utc).naive_utc()));
    }
    // Naive: "2026-11-14T10:00:00[.ffffff]" hoặc "2026-11-14 10:00:00[.ffffff]"
    for fmt in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f"] {
        if let Ok(dt) = NaiveDateTime::parse_from_str(s, fmt) {
            return Some(format_storage(dt));
        }
    }
    // Chỉ có ngày → 00:00:00
    if let Ok(d) = NaiveDate::parse_from_str(s, "%Y-%m-%d") {
        return Some(format_storage(d.and_hms_opt(0, 0, 0)?));
    }
    None
}

/// Chuỗi lưu DB → isoformat() của Python cho JSON response.
pub fn storage_to_iso(storage: &str) -> String {
    match NaiveDateTime::parse_from_str(storage, "%Y-%m-%d %H:%M:%S%.f") {
        Ok(dt) => py_isoformat(dt),
        Err(_) => storage.to_owned(),
    }
}

/// Parse chuỗi expires_at (isoformat aware của Python, hoặc legacy naive) →
/// DateTime UTC — tương đương as_utc(datetime.fromisoformat(s)) của Python.
pub fn parse_expiry(s: &str) -> Option<DateTime<Utc>> {
    let s = s.trim();
    if let Ok(dt) = DateTime::<FixedOffset>::parse_from_rfc3339(s) {
        return Some(dt.with_timezone(&Utc));
    }
    // Legacy naive → coi là UTC (as_utc)
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f") {
        return Some(dt.and_utc());
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f") {
        return Some(dt.and_utc());
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    #[test]
    fn storage_format_matches_sqlalchemy() {
        let dt = NaiveDate::from_ymd_opt(2026, 11, 14)
            .unwrap()
            .and_hms_micro_opt(4, 31, 15, 204921)
            .unwrap();
        assert_eq!(format_storage(dt), "2026-11-14 04:31:15.204921");
    }

    #[test]
    fn isoformat_matches_python() {
        let with_micro = NaiveDate::from_ymd_opt(2026, 1, 2)
            .unwrap()
            .and_hms_micro_opt(3, 4, 5, 123456)
            .unwrap();
        let no_micro = NaiveDate::from_ymd_opt(2026, 1, 2)
            .unwrap()
            .and_hms_opt(3, 4, 5)
            .unwrap();
        // SQLModel đọc về luôn gắn UTC → isoformat() có đuôi +00:00
        assert_eq!(py_isoformat(with_micro), "2026-01-02T03:04:05.123456+00:00");
        assert_eq!(py_isoformat(no_micro), "2026-01-02T03:04:05+00:00");
    }

    #[test]
    fn parse_rfc3339_converts_to_utc() {
        // Python/SQLAlchemy bind: có offset → quy đổi UTC trước khi lưu
        assert_eq!(
            parse_client_datetime("2026-11-14T17:00:00+07:00").unwrap(),
            "2026-11-14 10:00:00.000000"
        );
        assert_eq!(
            parse_client_datetime("2026-11-14T17:00:00Z").unwrap(),
            "2026-11-14 17:00:00.000000"
        );
        // Naive → giữ nguyên giờ wall
        assert_eq!(
            parse_client_datetime("2026-11-14 09:30:00").unwrap(),
            "2026-11-14 09:30:00.000000"
        );
        assert_eq!(
            parse_client_datetime("2026-11-14").unwrap(),
            "2026-11-14 00:00:00.000000"
        );
        assert!(parse_client_datetime("không phải ngày").is_none());
    }
}
