//! Chấm điểm địa điểm theo mức chi tiêu ghi nhận được — port 1:1 của
//! src/discovery.py. Không AI: chỉ thống kê và lọc. Mọi con số ở đây là *mức chi
//! ghi nhận tại địa điểm* (tổng bill chia số người), không phải giá món.
//!
//! Trọng số chưa được tinh chỉnh — giữ đúng như đề bài để sau này đo lại bằng
//! dữ liệu thật thay vì sửa cảm tính.

use serde_json::json;
use serde_json::Value;

pub const WEIGHTS_JSON: &str = r#"{"price": 0.35, "distance": 0.3, "category": 0.2, "history": 0.15}"#;
pub const MIN_SAMPLES: usize = 3;
pub const DEFAULT_RADIUS_KM: f64 = 3.0;
/// Band khởi điểm cho 3 loại phổ biến (VND/người). Loại khác không có band mặc
/// định — thà nói "chưa đủ dữ liệu" còn hơn bịa một khoảng giá.
pub const DEFAULT_BANDS: [(&str, i64, i64); 3] =
    [("cafe", 30_000, 60_000), ("restaurant", 50_000, 120_000), ("drink", 25_000, 50_000)];

/// Làm tròn VND theo bội số 1000 — int(round(v/1000) * 1000) của Python
/// (round() là banker's rounding).
fn round_vnd(value: f64) -> i64 {
    (value / 1000.0).round_ties_even() as i64 * 1000
}

/// round(x, 4) của Python — khớp cho các giá trị score phát sinh từ trọng số.
fn round4(x: f64) -> f64 {
    (x * 10_000.0).round_ties_even() / 10_000.0
}

/// round(x, 3) của Python cho distance_km.
fn round3(x: f64) -> f64 {
    (x * 1_000.0).round_ties_even() / 1_000.0
}

pub fn distance_km_rounded(km: f64) -> f64 {
    round3(km)
}

/// Nội suy tuyến tính, không cần numpy (dữ liệu mẫu rất nhỏ) — khớp
/// percentile() của src/discovery.py.
pub fn percentile(values: &[i64], fraction: f64) -> i64 {
    let mut ordered: Vec<i64> = values.to_vec();
    ordered.sort_unstable();
    if ordered.is_empty() {
        return 0;
    }
    if ordered.len() == 1 {
        return ordered[0];
    }
    let pos = (ordered.len() as f64 - 1.0) * fraction;
    let low = pos as usize; // int() của Python: cắt phần lẻ
    let high = (low + 1).min(ordered.len() - 1);
    let weight = pos - low as f64;
    round_vnd(ordered[low] as f64 * (1.0 - weight) + ordered[high] as f64 * weight)
}

/// Khoảng mức chi mỗi người của một loại địa điểm.
#[derive(Debug, Clone)]
pub struct Band {
    pub category: String,
    pub low: i64,
    pub high: i64,
    pub sample_size: usize,
    pub is_default: bool,
}

impl Band {
    pub fn contains(&self, value: Option<i64>) -> bool {
        match value {
            Some(v) => self.low <= v && v <= self.high,
            None => false,
        }
    }
}

/// Band của ``category`` từ lịch sử chi tiêu; ``None`` khi không đủ dữ liệu
/// và loại địa điểm không có band mặc định.
pub fn band_from(spend_per_person: &[i64], category: &str) -> Option<Band> {
    let samples: Vec<i64> = spend_per_person.iter().copied().filter(|v| *v > 0).collect();
    if samples.len() >= MIN_SAMPLES {
        let low = percentile(&samples, 0.25);
        let high = percentile(&samples, 0.75);
        return Some(Band {
            category: category.to_owned(),
            low,
            high: low.max(high),
            sample_size: samples.len(),
            is_default: false,
        });
    }
    DEFAULT_BANDS
        .iter()
        .find(|(c, _, _)| *c == category)
        .map(|(_, low, high)| Band {
            category: category.to_owned(),
            low: *low,
            high: *high,
            sample_size: samples.len(),
            is_default: true,
        })
}

/// 1.0 trong band, giảm dần ra ngoài trên độ rộng một band. Không có giá
/// ghi nhận → 0.0 (không bao giờ được điểm giá).
pub fn price_fit(value: Option<i64>, band: Option<&Band>) -> f64 {
    let (Some(value), Some(band)) = (value, band) else { return 0.0 };
    if band.low <= value && value <= band.high {
        return 1.0;
    }
    let span = (band.high - band.low).max(1) as f64;
    let gap = if value < band.low {
        band.low - value
    } else {
        value - band.high
    };
    (1.0 - gap as f64 / span).max(0.0)
}

pub fn distance_score(distance_km: Option<f64>, radius_km: f64) -> f64 {
    match distance_km {
        None => 0.0,
        Some(_d) if radius_km <= 0.0 => 0.0,
        Some(d) => (1.0 - (d / radius_km).min(1.0)).max(0.0),
    }
}

/// Quán đã ghi 3 lần trở lên = quán quen (H = 1.0).
pub fn visit_affinity(visits_at_place: usize) -> f64 {
    (visits_at_place as f64 / 3.0).min(1.0).max(0.0)
}

/// Trả ``(tổng điểm, từng thành phần)`` để UI giải thích được thứ hạng.
/// Tổng cộng theo đúng thứ tự Python: 0 + price·0.35 + distance·0.3 + category·0.2 + history·0.15.
pub fn score(
    spend_per_person: Option<i64>,
    band: Option<&Band>,
    distance_km: Option<f64>,
    category_affinity: f64,
    history_affinity: f64,
    radius_km: f64,
) -> (f64, Value) {
    let price = price_fit(spend_per_person, band);
    let distance = distance_score(distance_km, radius_km);
    let category = category_affinity.clamp(0.0, 1.0);
    let history = history_affinity.clamp(0.0, 1.0);
    let total = 0.0 + 0.35 * price + 0.3 * distance + 0.2 * category + 0.15 * history;
    let parts = json!({
        "price": round4(price),
        "distance": round4(distance),
        "category": round4(category),
        "history": round4(history),
    });
    (round4(total), parts)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vector do band_from() của Python tính ra.
    #[test]
    fn band_from_matches_python() {
        let b = band_from(&[30000, 45000, 50000, 75000, 100000], "cafe").unwrap();
        assert_eq!((b.low, b.high, b.sample_size, b.is_default), (45000, 75000, 5, false));

        let d = band_from(&[25000, 40000], "drink").unwrap();
        assert_eq!((d.low, d.high, d.sample_size, d.is_default), (25000, 50000, 2, true));

        assert!(band_from(&[], "bar").is_none()); // bar/other không có default

        let r = band_from(&[0, 50000, 60000, 70000], "restaurant").unwrap();
        assert_eq!((r.low, r.high, r.sample_size, r.is_default), (55000, 65000, 3, false));
    }

    /// Vector do price_fit() của Python tính ra.
    #[test]
    fn price_fit_matches_python() {
        let b = band_from(&[30000, 45000, 50000, 75000, 100000], "cafe").unwrap();
        assert_eq!(price_fit(Some(45000), Some(&b)), 1.0);
        assert!((price_fit(Some(20000), Some(&b)) - 0.16666666666666663).abs() < f64::EPSILON);
        assert_eq!(price_fit(None, Some(&b)), 0.0);
        assert_eq!(price_fit(Some(50000), None), 0.0);
        assert_eq!(price_fit(Some(110000), Some(&b)), 0.0);
    }

    /// Vector do distance_score()/visit_affinity() của Python tính ra.
    #[test]
    fn distance_and_visit_match_python() {
        assert!((distance_score(Some(1.0), 3.0) - 0.6666666666666667).abs() < f64::EPSILON);
        assert_eq!(distance_score(Some(3.5), 3.0), 0.0);
        assert_eq!(distance_score(None, 3.0), 0.0);
        assert_eq!(distance_score(Some(1.0), 0.0), 0.0);
        assert_eq!(visit_affinity(0), 0.0);
        assert!((visit_affinity(2) - 0.6666666666666666).abs() < f64::EPSILON);
        assert_eq!(visit_affinity(5), 1.0);
    }

    /// Vector do score() của Python tính ra.
    #[test]
    fn score_matches_python() {
        let b = band_from(&[30000, 45000, 50000, 75000, 100000], "cafe").unwrap();
        let (total, parts) = score(Some(45000), Some(&b), Some(1.0), 0.5, 1.0, 3.0);
        assert_eq!(total, 0.8);
        assert_eq!(
            parts,
            serde_json::json!({"price": 1.0, "distance": 0.6667, "category": 0.5, "history": 1.0})
        );

        let (total, parts) = score(None, None, None, 0.0, 0.0, 3.0);
        assert_eq!(total, 0.0);
        assert_eq!(
            parts,
            serde_json::json!({"price": 0.0, "distance": 0.0, "category": 0.0, "history": 0.0})
        );
    }

    #[test]
    fn band_contains() {
        let b = band_from(&[30000, 45000, 50000, 75000, 100000], "cafe").unwrap();
        assert!(b.contains(Some(45000)));
        assert!(b.contains(Some(75000)));
        assert!(!b.contains(Some(20000)));
        assert!(!b.contains(None));
    }

    /// Vector do _viewbox() của Python tính ra (repr f64 khớp byte).
    #[test]
    fn viewbox_matches_python() {
        assert_eq!(
            crate::osm::viewbox(10.776, 106.7, 2000),
            "106.6817112666605,10.79396622349982,106.7182887333395,10.758033776500179"
        );
    }
}
