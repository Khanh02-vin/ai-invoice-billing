//! Tiện ích địa điểm: khoảng cách trên mặt cầu và chuẩn hoá tên quán —
//! port 1:1 của src/geo.py.

use unicode_normalization::UnicodeNormalization;

pub const EARTH_RADIUS_KM: f64 = 6371.0;
/// Hai lần ghi cùng tên trong bán kính này là cùng một quán.
pub const SAME_PLACE_RADIUS_KM: f64 = 0.05;

pub fn haversine_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    2.0 * EARTH_RADIUS_KM * a.sqrt().asin()
}

/// Ký tự kết hợp sau NFD. Python lọc `unicodedata.combining(ch) != 0` — với tên
/// quán thực tế (tiếng Việt/Latin/Greek) tương đương khối U+0300–U+036F.
fn is_combining_mark(ch: char) -> bool {
    matches!(ch, '\u{0300}'..='\u{036F}')
}

/// Chữ thường, bỏ dấu, gộp khoảng trắng — để "Cà Phê Sữa" và "ca phe sua"
/// được coi là cùng một tên khi user ghi chi tiêu lần sau.
///
/// Tương đương casefold() của Python cho tên quán: to_lowercase chỉ khác biệt
/// ở ký tự hiếm (ß, ligature) không xuất hiện trong tên địa điểm tiếng Việt.
pub fn normalize_name(name: &str) -> String {
    let lowered = name.to_lowercase();
    let nfd: String = lowered.nfd().collect();
    let stripped: String = nfd.chars().filter(|c| !is_combining_mark(*c)).collect();
    let no_dd = stripped.replace('đ', "d");
    no_dd.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vector do normalize_name của Python sinh ra.
    #[test]
    fn normalize_name_matches_python() {
        let cases = [
            ("Cà Phê Sữa", "ca phe sua"),
            ("CƠM TẤM NGON", "com tam ngon"),
            ("  Bún   Đậu  ", "bun dau"),
            ("Đà Nẵng", "da nang"),
            ("Đặng Trần Cảm", "dang tran cam"),
            ("àáạãăâấậđéèêệíìóôốộơớợúùưựựýỵ", "aaaaaaaadeeeeiiooooooouuuuuyy"),
            ("", ""),
            ("Cafe Triệu Phú XYZ", "cafe trieu phu xyz"),
            ("Quán Chè Hương Xưa 2", "quan che huong xua 2"),
        ];
        for (input, expected) in cases {
            assert_eq!(normalize_name(input), expected, "input {input:?}");
        }
    }

    /// Vector do haversine_km của Python tính ra.
    #[test]
    fn haversine_matches_python() {
        let hn_hcm = haversine_km(21.0285, 105.8542, 10.7769, 106.6601);
        assert!((hn_hcm - 1143.1683221239105).abs() < 1e-9, "HN→HCM {hn_hcm}");
        let close = haversine_km(10.776, 106.7, 10.7765, 106.7004);
        assert!((close - 0.07071214977758512).abs() < 1e-12, "50m {close}");
    }

    #[test]
    fn same_place_radius_applies() {
        // ~70 m ngoài ngưỡng 50 m; cùng tọa độ thì trong ngưỡng
        assert!(haversine_km(10.776, 106.7, 10.7765, 106.7004) > SAME_PLACE_RADIUS_KM);
        assert!(haversine_km(10.776, 106.7, 10.7762, 106.7002) <= SAME_PLACE_RADIUS_KM);
    }
}
