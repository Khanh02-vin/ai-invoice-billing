//! Khám phá: hồ sơ mức chi theo loại địa điểm và danh sách quán gợi ý —
//! mirror src/routers/discover.py.
//!
//! Điểm xếp hạng = 0.35·giá + 0.30·khoảng cách + 0.20·loại quen + 0.15·quán quen.
//! Chưa tinh chỉnh — giữ nguyên trọng số đề bài đưa ra.
//!
//! Quán **chưa có mức chi ghi nhận** không được chấm điểm giá và không bao giờ
//! nhận huy hiệu "phù hợp mức chi tiêu": chúng nằm riêng trong ``unpriced``.

use std::collections::HashMap;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::Json;
use serde_json::{json, Value};

use crate::discovery::{
    band_from, distance_km_rounded, percentile, score, visit_affinity, Band, MIN_SAMPLES,
};
use crate::error::{AppError, AppResult, RequestId};
use crate::extract::CurrentUser;
use crate::geo::{haversine_km, normalize_name};
use crate::routes::places::{per_place_stats, spend_per_person};
use crate::routes::AppState;
use crate::store;
use crate::timefmt;
use crate::validation::{finish, query_float, query_float_gt, query_int_default};

const SPEND_LABEL: &str = "mức chi ghi nhận";
const VALID_CATEGORIES: [&str; 6] = ["cafe", "restaurant", "drink", "bakery", "bar", "other"];

fn weights_json() -> Value {
    // Thứ tự key của dict Python: price, distance, category, history
    json!({ "price": 0.35, "distance": 0.3, "category": 0.2, "history": 0.15 })
}

/// _bands: band từng loại + bảng hiển thị (p25/p75/mẫu/ước lượng ban đầu).
async fn bands_rows(
    state: &AppState,
    rid: &RequestId,
    user_id: &str,
) -> Result<(HashMap<String, Band>, Vec<Value>), AppError> {
    let places = store::search_places(&state.pool, rid, "", "").await?;
    let spends = store::list_spend_stats(&state.pool, rid, user_id).await?;

    let categories: HashMap<&str, &str> = places
        .iter()
        .map(|p| (p.id.as_str(), p.category.as_str()))
        .collect();
    let mut samples: HashMap<&str, Vec<i64>> = HashMap::new();
    for spend in &spends {
        if let Some(category) = categories.get(spend.place_id.as_str()) {
            samples
                .entry(category)
                .or_default()
                .push(spend_per_person(spend.amount, spend.party_size));
        }
    }

    let mut bands: HashMap<String, Band> = HashMap::new();
    let mut rows: Vec<Value> = Vec::new();
    // Thứ tự hàng khớp enum PlaceCategory của Python
    for category in ["cafe", "restaurant", "drink", "bakery", "bar", "other"] {
        append_band_row(&mut bands, &mut rows, &samples, category);
    }
    Ok((bands, rows))
}

fn append_band_row(
    bands: &mut HashMap<String, Band>,
    rows: &mut Vec<Value>,
    samples: &HashMap<&str, Vec<i64>>,
    category: &str,
) {
    let values = samples.get(category).cloned().unwrap_or_default();
    let band = band_from(&values, category);
    if let Some(b) = &band {
        bands.insert(category.to_owned(), b.clone());
    }
    rows.push(json!({
        "category": category,
        "low": band.as_ref().map(|b| b.low),
        "high": band.as_ref().map(|b| b.high),
        "median": if values.is_empty() { None } else { Some(percentile(&values, 0.5)) },
        "sample_size": values.len(),
        "is_default": band.as_ref().map(|b| b.is_default).unwrap_or(false),
    }));
}

/// GET /discover/bands
pub async fn bands(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
) -> AppResult<Json<Value>> {
    let (_, rows) = bands_rows(&state, &rid, &user.id).await?;
    Ok(Json(json!({
        "bands": rows,
        "spend_label": SPEND_LABEL,
        "weights": weights_json(),
        "min_samples": MIN_SAMPLES,
    })))
}

/// GET /discover?lat&lon&radius_km&category&q&limit
pub async fn discover(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Query(params): Query<HashMap<String, String>>,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let lat = query_float(&params, "lat", -90.0, 90.0, &mut fields);
    let lon = query_float(&params, "lon", -180.0, 180.0, &mut fields);
    let radius_km = query_float_gt(&params, "radius_km", 0.0, 50.0, &mut fields).unwrap_or(3.0);
    let category = params.get("category").cloned().unwrap_or_default();
    let q = params.get("q").cloned().unwrap_or_default();
    let limit = query_int_default(&params, "limit", 20, 1, 100, &mut fields);
    finish(fields)?;

    if lat.is_some() != lon.is_some() {
        return Err(AppError::new(
            "INVALID_COORDINATES",
            "Cần cả lat và lon.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    }
    if !category.is_empty() && !VALID_CATEGORIES.contains(&category.as_str()) {
        return Err(AppError::new(
            "INVALID_CATEGORY",
            "Loại địa điểm không hợp lệ.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    }

    let (bands, _) = bands_rows(&state, &rid, &user.id).await?;
    let stats = per_place_stats(&state, &rid, &user.id).await?;
    let visits_by_category: HashMap<String, i64> = store::category_visits(&state.pool, &rid, &user.id)
        .await?
        .into_iter()
        .map(|c| (c.category, c.visits))
        .collect();
    let total_visits: i64 = visits_by_category.values().sum();

    let normalized_q = if q.is_empty() { String::new() } else { normalize_name(&q) };
    let places = store::search_places(&state.pool, &rid, &normalized_q, &category).await?;

    let mut results: Vec<Value> = Vec::new();
    let mut unpriced: Vec<Value> = Vec::new();

    for place in &places {
        let mut distance_km: Option<f64> = None;
        if let (Some(ulat), Some(ulon), Some(plat), Some(plon)) =
            (lat, lon, place.lat, place.lon)
        {
            let km = distance_km_rounded(haversine_km(ulat, ulon, plat, plon));
            if km > radius_km {
                continue;
            }
            distance_km = Some(km);
        }

        let place_stats = stats.get(&place.id);
        let base = json!({
            "id": place.id,
            "name": place.name,
            "category": place.category,
            "address": place.address,
            "lat": place.lat,
            "lon": place.lon,
            "distance_km": distance_km,
            "visits": place_stats.map(|s| s.visits).unwrap_or(0),
            "last_visit": place_stats.map(|s| timefmt::storage_to_iso(&s.last_visit)),
        });

        let Some(ps) = place_stats else {
            unpriced.push(json!({
                "id": base["id"], "name": base["name"], "category": base["category"],
                "address": base["address"], "lat": base["lat"], "lon": base["lon"],
                "distance_km": distance_km, "visits": 0, "last_visit": Value::Null,
                "spend_per_person": Value::Null, "spend_label": SPEND_LABEL,
            }));
            continue;
        };

        let band = bands.get(&place.category);
        let category_affinity = if total_visits > 0 {
            visits_by_category
                .get(&place.category)
                .copied()
                .unwrap_or(0) as f64
                / total_visits as f64
        } else {
            0.0
        };
        let (total_score, parts) = score(
            Some(ps.median_spend),
            band,
            distance_km,
            category_affinity,
            visit_affinity(ps.visits),
            radius_km,
        );
        results.push(json!({
            "id": base["id"], "name": base["name"], "category": base["category"],
            "address": base["address"], "lat": base["lat"], "lon": base["lon"],
            "distance_km": distance_km, "visits": ps.visits,
            "last_visit": timefmt::storage_to_iso(&ps.last_visit),
            "score": total_score,
            "parts": parts,
            "spend_per_person": ps.median_spend,
            "spend_label": SPEND_LABEL,
            "band": band.map(|b| json!({"low": b.low, "high": b.high, "is_default": b.is_default})),
            "fits": band.map(|b| b.contains(Some(ps.median_spend))).unwrap_or(false),
        }));
    }

    // Điểm cao trước; cùng điểm thì gần trước, chưa có toạ độ xuống cuối.
    // f64 không có Ord — dùng total_cmp cho khoảng cách.
    let dist_key_cmp = |a: &Value, b: &Value| -> std::cmp::Ordering {
        a["distance_km"].is_null().cmp(&b["distance_km"].is_null()).then_with(|| {
            a["distance_km"]
                .as_f64()
                .unwrap_or(0.0)
                .total_cmp(&b["distance_km"].as_f64().unwrap_or(0.0))
        }).then_with(|| {
            a["name"].as_str().unwrap_or("").cmp(b["name"].as_str().unwrap_or(""))
        })
    };
    results.sort_by(|a, b| {
        let (sa, sb) = (a["score"].as_f64().unwrap_or(0.0), b["score"].as_f64().unwrap_or(0.0));
        sb.partial_cmp(&sa)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| dist_key_cmp(a, b))
    });
    unpriced.sort_by(|a, b| dist_key_cmp(a, b));

    Ok(Json(json!({
        "bounds": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "spend_label": SPEND_LABEL,
        "weights": weights_json(),
        "results": results.iter().take(limit as usize).cloned().collect::<Vec<_>>(),
        "unpriced": unpriced.iter().take(limit as usize).cloned().collect::<Vec<_>>(),
        "counts": {"priced": results.len(), "unpriced": unpriced.len()},
    })))
}
