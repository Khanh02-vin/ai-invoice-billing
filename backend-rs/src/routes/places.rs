//! Địa điểm: tìm kiếm, thêm mới — mirror src/routers/places.py.
//! (GET /places/nearby gợi ý OSM port sau — cần HTTP client.)

use std::collections::HashMap;

use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::Json;
use serde_json::{json, Value};

use crate::discovery::percentile;
use crate::error::{AppError, AppResult, RequestId};
use crate::extract::CurrentUser;
use crate::geo::{haversine_km, normalize_name, SAME_PLACE_RADIUS_KM};
use crate::routes::AppState;
use crate::security::new_id;
use crate::store::{self, PlaceRow, SpendStatRow};
use crate::timefmt;
use crate::validation::{
    body_object, check_len, finish, opt_float_field, parse_body, push_field, query_float,
    query_float_req, query_int, query_int_default, str_field,
};

const SPEND_LABEL: &str = "mức chi ghi nhận";
const VALID_CATEGORIES: [&str; 6] = ["cafe", "restaurant", "drink", "bakery", "bar", "other"];

/// Mức chi mỗi người — round(amount / max(party, 1)) banker's của Python.
/// (dùng chung với routes/spends.rs)
pub(crate) fn spend_per_person(amount: i64, party_size: i64) -> i64 {
    (amount as f64 / party_size.max(1) as f64).round_ties_even() as i64
}

/// spend_repo.per_place: gom lịch sử chi tiêu của user theo địa điểm.
/// Python chỉ nhóm spend có place tồn tại — caller chỉ tra theo place thật
/// nên kết quả tương đương.
#[derive(Clone)]
pub(crate) struct PlaceStats {
    pub(crate) visits: usize,
    pub(crate) median_spend: i64,
    pub(crate) last_visit: String,
}

pub(crate) async fn per_place_stats(
    state: &AppState,
    rid: &RequestId,
    user_id: &str,
) -> Result<HashMap<String, PlaceStats>, AppError> {
    let rows: Vec<SpendStatRow> = store::list_spend_stats(&state.pool, rid, user_id).await?;
    let mut groups: HashMap<String, Vec<&SpendStatRow>> = HashMap::new();
    for row in &rows {
        groups.entry(row.place_id.clone()).or_default().push(row);
    }
    let mut out = HashMap::new();
    for (place_id, grp) in groups {
        let values: Vec<i64> = grp
            .iter()
            .map(|r| spend_per_person(r.amount, r.party_size))
            .collect();
        // max(occurred_at): cùng format lưu trữ nên so chuỗi ≡ so thời gian
        let last_visit = grp
            .iter()
            .map(|r| r.occurred_at.as_str())
            .max()
            .unwrap_or("")
            .to_owned();
        out.insert(
            place_id,
            PlaceStats {
                visits: grp.len(),
                median_spend: percentile(&values, 0.5),
                last_visit,
            },
        );
    }
    Ok(out)
}

fn place_payload(
    place: &PlaceRow,
    stats: Option<&PlaceStats>,
    lat: Option<f64>,
    lon: Option<f64>,
) -> Value {
    let mut distance_m: Option<i64> = None;
    if let (Some(ulat), Some(ulon), Some(plat), Some(plon)) = (lat, lon, place.lat, place.lon) {
        // Python int(haversine * 1000): cắt phần lẻ, không làm tròn
        distance_m = Some((haversine_km(ulat, ulon, plat, plon) * 1000.0).trunc() as i64);
    }
    json!({
        "id": place.id,
        "name": place.name,
        "category": place.category,
        "address": place.address,
        "lat": place.lat,
        "lon": place.lon,
        "visits": stats.map(|s| s.visits).unwrap_or(0),
        "median_spend": stats.map(|s| s.median_spend),
        "last_visit": stats.map(|s| timefmt::storage_to_iso(&s.last_visit)),
        "distance_m": distance_m,
        "priced": stats.is_some(),
        "spend_label": SPEND_LABEL,
    })
}

/// Cùng tên và cách nhau ≤50 m → cùng một quán. Không có toạ độ ở một trong hai
/// bên thì không đo được khoảng cách; chỉ coi là trùng khi cả hai đều không có
/// toạ độ (tránh gộp nhầm hai quán cùng tên ở hai nơi khác nhau).
pub(crate) fn find_same_spot(candidates: &[PlaceRow], lat: Option<f64>, lon: Option<f64>) -> Option<PlaceRow> {
    candidates.iter().find(|p| {
        match (p.lat, p.lon, lat, lon) {
            (None, None, None, None) => true,
            (Some(plat), Some(plon), Some(ulat), Some(ulon)) => {
                haversine_km(plat, plon, ulat, ulon) <= SAME_PLACE_RADIUS_KM
            }
            _ => false,
        }
    }).cloned()
}

fn invalid_category(rid: &RequestId) -> AppError {
    AppError::new("INVALID_CATEGORY", "Loại địa điểm không hợp lệ.", StatusCode::BAD_REQUEST)
        .with_request_id(rid)
}

/// GET /places?q&category&lat&lon&radius_m&min_price&max_price
pub async fn list_places(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Query(params): Query<HashMap<String, String>>,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let q = params.get("q").cloned().unwrap_or_default();
    let category = params.get("category").cloned().unwrap_or_default();
    let lat = query_float(&params, "lat", -90.0, 90.0, &mut fields);
    let lon = query_float(&params, "lon", -180.0, 180.0, &mut fields);
    let radius_m = query_int_default(&params, "radius_m", 3000, 100, 20_000, &mut fields);
    let min_price = query_int(&params, "min_price", 0, i64::MAX, &mut fields);
    let max_price = query_int(&params, "max_price", 0, i64::MAX, &mut fields);
    finish(fields)?;

    if !category.is_empty() && !VALID_CATEGORIES.contains(&category.as_str()) {
        return Err(invalid_category(&rid));
    }

    let stats = per_place_stats(&state, &rid, &user.id).await?;
    let normalized_q = if q.is_empty() { String::new() } else { normalize_name(&q) };
    let places = store::search_places(&state.pool, &rid, &normalized_q, &category).await?;

    let filter_price = min_price.is_some() || max_price.is_some();
    let mut rows: Vec<Value> = Vec::new();
    for place in &places {
        let ps = stats.get(&place.id);
        // Lọc giá trên mức chi ghi nhận của chính user; place chưa có dữ liệu
        // bị loại vì không thể khẳng định nó khớp.
        if filter_price {
            let Some(ps) = ps else { continue };
            let spend = ps.median_spend;
            if let Some(min) = min_price {
                if spend < min {
                    continue;
                }
            }
            if let Some(max) = max_price {
                if spend > max {
                    continue;
                }
            }
        }
        let payload = place_payload(place, ps, lat, lon);
        if lat.is_some() && lon.is_some() {
            if let Some(d) = payload["distance_m"].as_i64() {
                if d > radius_m {
                    continue;
                }
            }
        }
        rows.push(payload);
    }

    // Gần trước; địa điểm chưa có toạ độ xuống cuối chứ không bị giấu.
    rows.sort_by_key(|r| {
        (
            r["distance_m"].is_null(),
            r["distance_m"].as_i64().unwrap_or(0),
            r["name"].as_str().unwrap_or("").to_owned(),
        )
    });
    Ok(Json(json!({ "places": rows, "spend_label": SPEND_LABEL })))
}

/// GET /places/nearby — gợi ý quán quanh đây từ OSM. Không có giá → priced: false.
pub async fn nearby(
    State(state): State<AppState>,
    _rid: RequestId,
    _user: CurrentUser,
    Query(params): Query<HashMap<String, String>>,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let lat = query_float_req(&params, "lat", -90.0, 90.0, &mut fields);
    let lon = query_float_req(&params, "lon", -180.0, 180.0, &mut fields);
    let radius_m = query_int_default(&params, "radius_m", 2000, 100, 5000, &mut fields);
    finish(fields)?;

    let places = crate::osm::nearby_places(&state.http, lat, lon, radius_m, 20).await;
    Ok(Json(json!({
        "places": places,
        "source": "osm",
        "spend_label": SPEND_LABEL,
    })))
}

/// POST /places — Cùng tên và ≤50 m là cùng một quán; trả quán đã có.
pub async fn create_place(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (name, category, address, lat, lon) = match obj {
        Some(o) => (
            str_field(o, "name", "", &mut fields),
            str_field(o, "category", "other", &mut fields),
            str_field(o, "address", "", &mut fields),
            opt_float_field(o, "lat", &mut fields),
            opt_float_field(o, "lon", &mut fields),
        ),
        None => (String::new(), "other".to_owned(), String::new(), None, None),
    };
    check_len(&name, "name", 1, 120, &mut fields);
    check_len(&address, "address", 0, 300, &mut fields);
    if let Some(v) = lat {
        if !(-90.0..=90.0).contains(&v) {
            push_field(&mut fields, "lat");
        }
    }
    if let Some(v) = lon {
        if !(-180.0..=180.0).contains(&v) {
            push_field(&mut fields, "lon");
        }
    }
    finish(fields)?;

    if !VALID_CATEGORIES.contains(&category.as_str()) {
        return Err(invalid_category(&rid));
    }
    let normalized = normalize_name(&name);
    if normalized.is_empty() {
        return Err(AppError::new(
            "INVALID_NAME",
            "Tên địa điểm không hợp lệ.",
            StatusCode::BAD_REQUEST,
        )
        .with_request_id(&rid));
    }

    let candidates = store::find_match_candidates(&state.pool, &rid, &normalized).await?;
    let mut place = find_same_spot(&candidates, lat, lon);
    let created = place.is_none();
    if place.is_none() {
        let row = store::PlaceInsert {
            id: new_id(),
            name: name.trim().to_owned(),
            normalized_name: normalized,
            category: category.clone(),
            address: address.trim().to_owned(),
            lat,
            lon,
            created_by: Some(user.id.clone()),
            created_at: timefmt::now_storage(),
        };
        store::insert_place(&state.pool, &rid, &row).await?;
        place = Some(PlaceRow {
            id: row.id.clone(),
            name: row.name.clone(),
            normalized_name: row.normalized_name.clone(),
            category: row.category.clone(),
            address: row.address.clone(),
            lat: row.lat,
            lon: row.lon,
            created_by: row.created_by.clone(),
            created_at: row.created_at.clone(),
        });
    }
    let place = place.expect("đã tạo hoặc tìm thấy");
    let stats = per_place_stats(&state, &rid, &user.id)
        .await?
        .get(&place.id)
        .cloned();
    Ok(Json(json!({
        "place": place_payload(&place, stats.as_ref(), None, None),
        "created": created,
    })))
}
