//! Gợi ý địa điểm quanh đây từ OpenStreetMap (Nominatim) — port src/places_osm.py.
//!
//! OSM **không có giá**. Mọi kết quả ở đây trả ``priced: false`` và không bao giờ
//! được chấm điểm "phù hợp mức chi tiêu" — chấm giá cho dữ liệu chưa ai ghi lại
//! là bịa số. Chúng chỉ dùng để gợi ý tên/toạ độ khi user thêm địa điểm.

use std::time::Duration;

use serde_json::{json, Value};

use crate::geo::haversine_km;

pub const NOMINATIM_URL: &str = "https://nominatim.openstreetmap.org";
pub const USER_AGENT: &str = "FoodDiscoveryMVP/1.0 (contact: admin@localhost)";
const TIMEOUT: Duration = Duration::from_secs(4);

/// Khung nhìn bao quanh (lat, lon) theo bán kính — thay cho ±0.1° cố định.
pub fn viewbox(lat: f64, lon: f64, radius_m: i64) -> String {
    let dlat = radius_m as f64 / 111_320.0;
    let dlon = dlat / lat.to_radians().cos().max(0.01);
    format!("{},{},{},{}", lon - dlon, lat + dlat, lon + dlon, lat - dlat)
}

/// float(item["lat"]) của Python: chấp nhận chuỗi hoặc số; lỗi → None.
fn item_f64(item: &Value, key: &str) -> Option<f64> {
    match item.get(key) {
        Some(Value::String(s)) => s.trim().parse().ok(),
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

fn item_str<'a>(item: &'a Value, key: &str) -> &'a str {
    item.get(key).and_then(Value::as_str).unwrap_or("")
}

/// Phần lọc/định hình kết quả Nominatim — tách riêng để test không cần mạng.
/// Đúng hành vi nearby_places() của Python: sai toạ độ bỏ, ngoài bán kính bỏ,
/// tên rỗng rơi về display_name[:80] rồi "Địa điểm", sắp theo khoảng cách.
pub fn parse_nearby_entries(lat: f64, lon: f64, radius_m: i64, limit: usize, entries: &Value) -> Vec<Value> {
    let Some(list) = entries.as_array() else {
        return Vec::new();
    };
    let mut places: Vec<Value> = Vec::new();
    for item in list {
        let (Some(p_lat), Some(p_lon)) = (item_f64(item, "lat"), item_f64(item, "lon")) else {
            continue;
        };
        // Python int(haversine * 1000): cắt phần lẻ
        let distance_m = (haversine_km(lat, lon, p_lat, p_lon) * 1000.0).trunc() as i64;
        if distance_m > radius_m {
            continue;
        }
        let name = {
            let name = item_str(item, "name");
            let display = item_str(item, "display_name");
            if !name.is_empty() {
                name.to_owned()
            } else if !display.is_empty() {
                display.chars().take(80).collect()
            } else {
                "Địa điểm".to_owned()
            }
        };
        let kind = {
            let t = item_str(item, "type");
            if t.is_empty() {
                "place"
            } else {
                t
            }
        };
        places.push(json!({
            "name": name,
            "kind": kind,
            "lat": p_lat,
            "lon": p_lon,
            "distance_m": distance_m,
            "source": "osm",
            "priced": false,
        }));
    }
    places.sort_by_key(|p| p["distance_m"].as_i64().unwrap_or(i64::MAX));
    places.truncate(limit);
    places
}

/// Quán ăn/uống quanh (lat, lon) trong ``radius_m``. Lỗi mạng → danh sách rỗng.
pub async fn nearby_places(
    client: &reqwest::Client,
    lat: f64,
    lon: f64,
    radius_m: i64,
    limit: usize,
) -> Vec<Value> {
    let params = [
        ("format", "jsonv2".to_string()),
        ("q", "cafe restaurant shop".to_string()),
        ("viewbox", viewbox(lat, lon, radius_m)),
        ("bounded", "1".to_string()),
        ("limit", (limit * 2).to_string()),
    ];
    let response = client
        .get(format!("{NOMINATIM_URL}/search"))
        .query(&params)
        .header("User-Agent", USER_AGENT)
        .timeout(TIMEOUT)
        .send()
        .await;
    let entries: Value = match response {
        Ok(resp) if resp.status().is_success() => match resp.json().await {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        },
        _ => return Vec::new(),
    };
    parse_nearby_entries(lat, lon, radius_m, limit, &entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Vector do nearby_places() của Python tính ra với httpx được mock —
    /// đúng thứ tự, đúng tên cắt [:80] ký tự, đúng phần tử bị loại.
    #[test]
    fn parse_nearby_matches_python() {
        let entries = json!([
            {"lat": "10.7765", "lon": "106.7005", "name": "Cà Phê OSM", "type": "cafe", "display_name": "Cà Phê OSM, Quận 1"},
            {"lat": "10.7900", "lon": "106.7100", "name": "", "type": "restaurant",
             "display_name": "Nhà hàng X đường tên rất dài ".repeat(10)},
            {"lat": "bad", "lon": "106.7", "name": "Sai toạ độ", "type": "cafe"},
            {"lon": "106.7", "name": "Thiếu lat", "type": "shop"},
            {"lat": 10.7762, "lon": 106.7002, "name": null, "type": null, "display_name": null},
        ]);
        let got = parse_nearby_entries(10.776, 106.7, 2000, 20, &entries);
        assert_eq!(got.len(), 3);

        // Gần nhất: name None + display None → "Địa điểm", toạ độ dạng number OK
        assert_eq!(got[0]["name"], "Địa điểm");
        assert_eq!(got[0]["kind"], "place");
        assert_eq!(got[0]["distance_m"], 31);

        assert_eq!(got[1]["name"], "Cà Phê OSM");
        assert_eq!(got[1]["distance_m"], 77);

        // Tên dài → cắt đúng 80 ký tự (không phải 80 byte)
        let long_name = got[2]["name"].as_str().unwrap();
        assert_eq!(long_name.chars().count(), 80);
        assert!(long_name.starts_with("Nhà hàng X đường tên rất dài"));
        assert_eq!(got[2]["distance_m"], 1901);
        assert_eq!(got[2]["kind"], "restaurant");

        for p in &got {
            assert_eq!(p["source"], "osm");
            assert_eq!(p["priced"], false);
        }
    }

    #[test]
    fn entries_khong_phai_mang_tra_rong() {
        assert!(parse_nearby_entries(10.0, 106.0, 2000, 20, &json!({"x": 1})).is_empty());
        assert!(parse_nearby_entries(10.0, 106.0, 2000, 20, &json!("rác")).is_empty());
    }

    #[test]
    fn limit_cat_ket_qua() {
        let entries = json!([
            {"lat": "10.770", "lon": "106.700"},
            {"lat": "10.771", "lon": "106.700"},
            {"lat": "10.772", "lon": "106.700"},
        ]);
        let got = parse_nearby_entries(10.776, 106.7, 5000, 2, &entries);
        assert_eq!(got.len(), 2);
        // giữ hai quán GẦN nhất: 10.772 (Δ0.004°) và 10.771 (Δ0.005°), bỏ 10.770
        assert_eq!(got[0]["lat"], 10.772);
        assert_eq!(got[1]["lat"], 10.771);
    }
}
