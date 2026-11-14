//! Rate limiting — sliding window, in-memory — port src/security/rate_limit.py
//! (backend mặc định memory; backend SQLite để persistence giữa worker port sau).
//!
//! Sliding window: chính xác hơn fixed window, không bị burst ở ranh giới.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

fn now_epoch_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Tạo key chuẩn: ưu tiên user_id nếu có, không thì IP + path.
pub fn make_key(client_ip: &str, path: &str, user_id: Option<&str>) -> String {
    match user_id {
        Some(uid) if !uid.is_empty() => format!("uid:{uid}:{path}"),
        _ => format!("ip:{client_ip}:{path}"),
    }
}

/// Backend in-memory: key → danh sách timestamp các hit.
#[derive(Default)]
pub struct RateLimiter {
    hits: Mutex<HashMap<String, Vec<f64>>>,
}

impl RateLimiter {
    /// Xoá hit cũ hơn window (giữ hit > cutoff — khớp Python `t > cutoff`).
    fn prune(hits: &mut Vec<f64>, window_seconds: f64, now: f64) {
        let cutoff = now - window_seconds;
        hits.retain(|t| *t > cutoff);
    }

    /// Kiểm tra request có được cho phép không; cho phép thì GHI NHẬN hit.
    pub fn is_allowed(&self, key: &str, max_requests: i64, window_seconds: f64) -> bool {
        let now = now_epoch_f64();
        let mut map = self.hits.lock().expect("rate limit lock");
        let entry = map.entry(key.to_owned()).or_default();
        Self::prune(entry, window_seconds, now);
        if entry.len() as i64 >= max_requests {
            return false;
        }
        entry.push(now);
        true
    }

    /// Số request còn lại trong window hiện tại.
    pub fn get_remaining(&self, key: &str, max_requests: i64, window_seconds: f64) -> i64 {
        let now = now_epoch_f64();
        let mut map = self.hits.lock().expect("rate limit lock");
        let entry = map.entry(key.to_owned()).or_default();
        Self::prune(entry, window_seconds, now);
        (max_requests - entry.len() as i64).max(0)
    }

    /// Xoá toàn bộ state — dùng cho test.
    pub fn clear(&self) {
        self.hits.lock().expect("rate limit lock").clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn make_key_matches_python() {
        assert_eq!(make_key("127.0.0.1", "rate_limit_auth_login", None), "ip:127.0.0.1:rate_limit_auth_login");
        assert_eq!(make_key("x", "p", Some("u1")), "uid:u1:p");
    }

    #[test]
    fn allows_up_to_max_then_blocks() {
        let rl = RateLimiter::default();
        for _ in 0..3 {
            assert!(rl.is_allowed("k", 3, 60.0));
        }
        assert!(!rl.is_allowed("k", 3, 60.0));
        // request bị chặn KHÔNG ghi hit (khớp Python: return False không add)
        assert_eq!(rl.get_remaining("k", 3, 60.0), 0);
        assert!(!rl.is_allowed("k", 3, 60.0));
    }

    #[test]
    fn window_prunes_old_hits() {
        // Mô phỏng prune qua get_remaining sau khi thủ công nạp hit cũ
        let rl = RateLimiter::default();
        {
            let mut map = rl.hits.lock().unwrap();
            map.insert(
                "k".into(),
                vec![0.0, 0.5], // hit rất cũ (epoch ~0)
            );
        }
        // hit cũ hơn window 60s bị prune → còn đủ 5
        assert_eq!(rl.get_remaining("k", 5, 60.0), 5);
    }

    #[test]
    fn keys_independent() {
        let rl = RateLimiter::default();
        assert!(rl.is_allowed("a", 1, 60.0));
        assert!(!rl.is_allowed("a", 1, 60.0));
        assert!(rl.is_allowed("b", 1, 60.0));
    }
}
