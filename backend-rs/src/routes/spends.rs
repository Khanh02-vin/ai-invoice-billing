//! Ghi một lần chi tiêu tại địa điểm, xem lịch sử, xoá, và đọc số tiền từ QR —
//! mirror src/routers/spends.py. Draft confirm port sau (cần geo.normalize_name).

use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};
use base64::Engine as _;
use std::collections::HashMap;

use crate::error::{AppError, AppResult, RequestId};
use crate::extract::CurrentUser;
use crate::geo::normalize_name;
use crate::qr;
use crate::routes::places::{find_same_spot, spend_per_person};
use crate::routes::AppState;
use crate::security::new_id;
use crate::store::{self, DraftRow, PlaceRow, SpendListRow};
use crate::timefmt;
use crate::validation::{
    body_object, check_len, finish, int_field, int_field_default, opt_datetime_field,
    opt_float_field, opt_str_field, parse_body, push_query_field, req_str_field, str_field,
};

const SPEND_LABEL: &str = "mức chi ghi nhận";

fn spend_payload(
    id: &str,
    place_id: &str,
    place_name: &str,
    category: &str,
    amount: i64,
    party_size: i64,
    occurred_at_storage: &str,
    note: &str,
    has_receipt: bool,
) -> Value {
    json!({
        "id": id,
        "place_id": place_id,
        "place_name": place_name,
        "category": category,
        "amount": amount,
        "party_size": party_size,
        "spend_per_person": spend_per_person(amount, party_size),
        "occurred_at": timefmt::storage_to_iso(occurred_at_storage),
        "note": note,
        "receipt": has_receipt,
        "spend_label": SPEND_LABEL,
    })
}

fn draft_payload(d: &DraftRow) -> Value {
    json!({
        "id": d.id,
        "amount": d.amount,
        "party_size": d.party_size,
        "merchant_name": d.merchant_name,
        "bank_bin": d.bank_bin,
        "account": d.account,
        "place_id": d.place_id,
        "lat": d.lat,
        "lon": d.lon,
        "note": d.note,
        "created_at": timefmt::storage_to_iso(&d.created_at),
    })
}

/// POST /spends
pub async fn create_spend(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (place_id, amount, party_size, occurred_at, note) = match obj {
        Some(o) => (
            str_field(o, "place_id", "", &mut fields),
            int_field(o, "amount", 1, 100_000_000, &mut fields),
            int_field_default(o, "party_size", 1, 1, 50, &mut fields),
            opt_datetime_field(o, "occurred_at", &mut fields),
            str_field(o, "note", "", &mut fields),
        ),
        None => (String::new(), None, 1, None, String::new()),
    };
    check_len(&place_id, "place_id", 1, 32, &mut fields);
    check_len(&note, "note", 0, 200, &mut fields);
    finish(fields)?;

    let place = store::get_place_info(&state.pool, &rid, &place_id).await?;
    let Some(place) = place else {
        return Err(AppError::new(
            "PLACE_NOT_FOUND",
            "Chưa có địa điểm này.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    };

    // finish() đã bảo đảm amount/party hợp lệ
    let amount = amount.expect("amount đã qua validation");
    let spend_id = new_id();
    let occurred = occurred_at.unwrap_or_else(timefmt::now_storage);
    let row = store::SpendInsert {
        id: spend_id.clone(),
        user_id: user.id,
        place_id: place_id.clone(),
        amount,
        party_size,
        occurred_at: occurred.clone(),
        note: note.trim().to_owned(),
        created_at: timefmt::now_storage(),
    };
    store::insert_spend(&state.pool, &rid, &row).await?;
    Ok(Json(json!({
        "spend": spend_payload(
            &spend_id,
            &place_id,
            &place.name,
            &place.category,
            amount,
            party_size,
            &occurred,
            &note.trim(),
            false,
        )
    })))
}

/// GET /spends?start&end
pub async fn list_spends(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Query(params): Query<HashMap<String, String>>,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let start = parse_query_datetime(&params, "start", &mut fields);
    let end = parse_query_datetime(&params, "end", &mut fields);
    if let (Some(s), Some(e)) = (&start, &end) {
        if s > e {
            return Err(AppError::new(
                "INVALID_DATE_RANGE",
                "Khoảng ngày không hợp lệ.",
                StatusCode::BAD_REQUEST,
            )
            .with_request_id(&rid));
        }
    }
    finish(fields)?;

    let rows = store::list_spends(
        &state.pool,
        &rid,
        &user.id,
        start.as_deref(),
        end.as_deref(),
    )
    .await?;
    let spends: Vec<Value> = rows
        .iter()
        .map(|r: &SpendListRow| {
            spend_payload(
                &r.id,
                &r.place_id,
                r.place_name.as_deref().unwrap_or(""),
                r.category.as_deref().unwrap_or(""),
                r.amount,
                r.party_size,
                &r.occurred_at,
                &r.note,
                r.receipt_photo.is_some(),
            )
        })
        .collect();
    Ok(Json(json!({ "spends": spends, "spend_label": SPEND_LABEL })))
}

fn parse_query_datetime(
    params: &HashMap<String, String>,
    key: &str,
    fields: &mut Vec<Value>,
) -> Option<String> {
    match params.get(key) {
        None => None,
        Some(raw) => match timefmt::parse_client_datetime(raw) {
            Some(stored) => Some(stored),
            None => {
                push_query_field(fields, key);
                None
            }
        },
    }
}

/// DELETE /spends/{spend_id}
pub async fn delete_spend(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Path(spend_id): Path<String>,
) -> AppResult<Json<Value>> {
    let owner = store::get_spend_owner(&state.pool, &rid, &spend_id).await?;
    match owner {
        None => Err(AppError::new(
            "SPEND_NOT_FOUND",
            "Không tìm thấy lần chi này.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid)),
        Some(owner) if owner != user.id => Err(AppError::new(
            "FORBIDDEN",
            "Đây không phải lần chi của bạn.",
            StatusCode::FORBIDDEN,
        )
        .with_request_id(&rid)),
        Some(_) => {
            store::delete_spend(&state.pool, &rid, &user.id, &spend_id).await?;
            Ok(Json(json!({ "deleted": true })))
        }
    }
}

/// POST /spends/parse-qr — public, không cần đăng nhập (như Python).
/// rid không nằm trong body 200 nhưng dùng chung contract khi có lỗi.
pub async fn parse_qr(_rid: RequestId, body: Bytes) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let text = match obj {
        Some(o) => str_field(o, "text", "", &mut fields),
        None => String::new(),
    };
    check_len(&text, "text", 0, 4000, &mut fields);
    finish(fields)?;

    let parsed = qr::parse_qr_text(&text);
    Ok(Json(json!({
        "amount": parsed.amount,
        "party_size": parsed.party_size,
        "merchant_name": parsed.merchant_name,
        "bank_bin": parsed.bank_bin,
        "account": parsed.account,
        "raw": parsed.raw,
    })))
}

/// POST /spends/drafts — giao dịch tạm trước khi chuyển sang app ngân hàng.
pub async fn create_draft(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj: Option<&Map<String, Value>> = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (amount, party_size, merchant_name, bank_bin, account, place_id, lat, lon, note) =
        match obj {
            Some(o) => (
                int_field(o, "amount", 1, 100_000_000, &mut fields),
                int_field_default(o, "party_size", 1, 1, 50, &mut fields),
                str_field(o, "merchant_name", "", &mut fields),
                str_field(o, "bank_bin", "", &mut fields),
                str_field(o, "account", "", &mut fields),
                opt_str_field(o, "place_id", &mut fields),
                opt_float_field(o, "lat", &mut fields),
                opt_float_field(o, "lon", &mut fields),
                str_field(o, "note", "", &mut fields),
            ),
            None => (
                None,
                1,
                String::new(),
                String::new(),
                String::new(),
                None,
                None,
                None,
                String::new(),
            ),
        };
    check_len(&merchant_name, "merchant_name", 0, 120, &mut fields);
    check_len(&bank_bin, "bank_bin", 0, 6, &mut fields);
    check_len(&account, "account", 0, 32, &mut fields);
    check_len(&note, "note", 0, 200, &mut fields);
    finish(fields)?;

    let amount = amount.expect("amount đã qua validation");
    let draft_id = new_id();
    let created_at = timefmt::now_storage();
    let row = store::DraftInsert {
        id: draft_id.clone(),
        user_id: user.id,
        amount,
        party_size,
        merchant_name: merchant_name.trim().to_owned(),
        bank_bin: bank_bin.trim().to_owned(),
        account: account.trim().to_owned(),
        place_id,
        lat,
        lon,
        note: note.trim().to_owned(),
        created_at: created_at.clone(),
    };
    store::insert_draft(&state.pool, &rid, &row).await?;
    Ok(Json(json!({ "draft": {
        "id": draft_id,
        "amount": amount,
        "party_size": party_size,
        "merchant_name": row.merchant_name,
        "bank_bin": row.bank_bin,
        "account": row.account,
        "place_id": row.place_id,
        "lat": row.lat,
        "lon": row.lon,
        "note": row.note,
        "created_at": timefmt::storage_to_iso(&created_at),
    } })))
}

/// GET /spends/drafts
pub async fn list_drafts(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
) -> AppResult<Json<Value>> {
    let rows = store::list_drafts(&state.pool, &rid, &user.id).await?;
    let drafts: Vec<Value> = rows.iter().map(draft_payload).collect();
    Ok(Json(json!({ "drafts": drafts })))
}

/// DELETE /spends/drafts/{draft_id} — sai user cũng 404 (khớp Python).
pub async fn delete_draft(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Path(draft_id): Path<String>,
) -> AppResult<Json<Value>> {
    if store::delete_draft_owned(&state.pool, &rid, &user.id, &draft_id).await? {
        Ok(Json(json!({ "deleted": true })))
    } else {
        Err(AppError::new(
            "DRAFT_NOT_FOUND",
            "Không tìm thấy giao dịch tạm.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid))
    }
}

/// POST /spends/drafts/{draft_id}/confirm — ghim draft thành lần chi chính thức:
/// dùng place đã ghim, hoặc tạo/tìm place theo tên + toạ độ lúc quét.
pub async fn confirm_draft(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Path(draft_id): Path<String>,
    body: Bytes,
) -> AppResult<Json<Value>> {
    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let (category, place_name, address, note) = match obj {
        Some(o) => (
            str_field(o, "category", "other", &mut fields),
            opt_str_field(o, "place_name", &mut fields),
            str_field(o, "address", "", &mut fields),
            opt_str_field(o, "note", &mut fields),
        ),
        None => ("other".to_owned(), None, String::new(), None),
    };
    check_len(&category, "category", 0, 32, &mut fields);
    if let Some(pn) = &place_name {
        check_len(pn, "place_name", 0, 120, &mut fields);
    }
    check_len(&address, "address", 0, 200, &mut fields);
    if let Some(n) = &note {
        check_len(n, "note", 0, 200, &mut fields);
    }
    finish(fields)?;

    let draft = store::get_draft(&state.pool, &rid, &draft_id).await?;
    let Some(draft) = draft else {
        return Err(AppError::new(
            "DRAFT_NOT_FOUND",
            "Không tìm thấy giao dịch tạm.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    };
    if draft.user_id != user.id {
        return Err(AppError::new(
            "FORBIDDEN",
            "Đây không phải giao dịch của bạn.",
            StatusCode::FORBIDDEN,
        )
        .with_request_id(&rid));
    }

    let place = match &draft.place_id {
        Some(pid) => store::get_place(&state.pool, &rid, pid).await?,
        None => None,
    };
    let place = match place {
        Some(p) => p,
        None => {
            // (body.place_name or draft.merchant_name or "Địa điểm chưa rõ").strip()
            let name = place_name
                .as_deref()
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    if draft.merchant_name.is_empty() {
                        None
                    } else {
                        Some(draft.merchant_name.as_str())
                    }
                })
                .unwrap_or("Địa điểm chưa rõ")
                .trim()
                .to_owned();
            let normalized = normalize_name(&name);
            let candidates =
                store::find_match_candidates(&state.pool, &rid, &normalized).await?;
            match find_same_spot(&candidates, draft.lat, draft.lon) {
                Some(p) => p,
                None => {
                    let row = store::PlaceInsert {
                        id: new_id(),
                        name,
                        normalized_name: normalized,
                        category: if category.is_empty() { "other".to_owned() } else { category },
                        address: address.trim().to_owned(),
                        lat: draft.lat,
                        lon: draft.lon,
                        created_by: Some(user.id.clone()),
                        created_at: timefmt::now_storage(),
                    };
                    store::insert_place(&state.pool, &rid, &row).await?;
                    PlaceRow {
                        id: row.id.clone(),
                        name: row.name.clone(),
                        normalized_name: row.normalized_name.clone(),
                        category: row.category.clone(),
                        address: row.address.clone(),
                        lat: row.lat,
                        lon: row.lon,
                        created_by: row.created_by.clone(),
                        created_at: row.created_at.clone(),
                    }
                }
            }
        }
    };

    let spend_id = new_id();
    let occurred = timefmt::now_storage();
    // (body.note if body.note is not None else draft.note).strip()
    let final_note = note.unwrap_or_else(|| draft.note.clone()).trim().to_owned();
    store::insert_spend(
        &state.pool,
        &rid,
        &store::SpendInsert {
            id: spend_id.clone(),
            user_id: user.id.clone(),
            place_id: place.id.clone(),
            amount: draft.amount,
            party_size: draft.party_size,
            occurred_at: occurred.clone(),
            note: final_note.clone(),
            created_at: timefmt::now_storage(),
        },
    )
    .await?;
    store::delete_draft_owned(&state.pool, &rid, &user.id, &draft_id).await?;
    Ok(Json(json!({
        "spend": spend_payload(
            &spend_id,
            &place.id,
            &place.name,
            &place.category,
            draft.amount,
            draft.party_size,
            &occurred,
            &final_note,
            false,
        )
    })))
}


// ---- Ảnh biên lai chuyển khoản ----

const RECEIPT_MAX_BYTES: usize = 2_500_000;

fn receipts_dir(settings: &crate::settings::Settings) -> std::path::PathBuf {
    std::path::Path::new(&settings.database_path)
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .join("receipts")
}

/// POST /spends/{spend_id}/receipt — lưu ảnh biên lai (data URL JPEG/PNG/WebP,
/// ≤ 2.5MB) vào đĩa kèm giao dịch. Chỉ owner.
pub async fn upload_receipt(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Path(spend_id): Path<String>,
    body: Bytes,
) -> AppResult<Json<Value>> {
    // spend_id dùng làm tên file — chỉ chấp nhận ký tự an toàn (id sinh ra là hex)
    if spend_id.is_empty() || !spend_id.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(AppError::new(
            "NOT_FOUND",
            "Không tìm thấy lần chi này.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    }
    let owner = store::get_spend_owner(&state.pool, &rid, &spend_id).await?;
    if owner.as_deref() != Some(user.id.as_str()) {
        return Err(AppError::new(
            "SPEND_NOT_FOUND",
            "Không tìm thấy lần chi này.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    }

    let mut fields = Vec::new();
    let parsed = parse_body(&body, &mut fields);
    let obj = parsed.as_ref().and_then(|v| body_object(v, &mut fields));
    let image = match obj {
        Some(o) => req_str_field(o, "image", &mut fields),
        None => {
            crate::validation::push_field(&mut fields, "image");
            String::new()
        }
    };
    finish(fields)?;

    let Some((mime, b64)) = image.split_once("base64,") else {
        return Err(AppError::validation(vec![json!(["body", "image"])]));
    };
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64.trim())
        .map_err(|_| AppError::validation(vec![json!(["body", "image"])]))?;
    if bytes.is_empty() {
        return Err(AppError::validation(vec![json!(["body", "image"])]));
    }
    if bytes.len() > RECEIPT_MAX_BYTES {
        return Err(AppError::new(
            "PAYLOAD_TOO_LARGE",
            "Ảnh biên lai quá lớn (tối đa 2.5MB).",
            StatusCode::PAYLOAD_TOO_LARGE,
        )
        .with_request_id(&rid));
    }

    let ext = match mime {
        "data:image/png" => "png",
        "data:image/webp" => "webp",
        _ => "jpg",
    };
    let dir = receipts_dir(&state.settings);
    tokio::fs::create_dir_all(&dir)
        .await
        .map_err(|e| crate::store::internal_debug(&rid, &e))?;
    let path = dir.join(format!("{spend_id}.{ext}"));
    tokio::fs::write(&path, &bytes)
        .await
        .map_err(|e| crate::store::internal_debug(&rid, &e))?;
    let relative = format!(
        "receipts/{}",
        path.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()
    );
    store::set_receipt_photo(&state.pool, &rid, &user.id, &spend_id, &relative).await?;
    Ok(Json(json!({ "saved": true, "receipt": relative })))
}

/// GET /spends/{spend_id}/receipt — đọc lại ảnh biên lai (chỉ owner).
pub async fn get_receipt(
    State(state): State<AppState>,
    rid: RequestId,
    user: CurrentUser,
    Path(spend_id): Path<String>,
) -> AppResult<Response> {
    let owner = store::get_spend_owner(&state.pool, &rid, &spend_id).await?;
    if owner.as_deref() != Some(user.id.as_str()) {
        return Err(AppError::new(
            "SPEND_NOT_FOUND",
            "Không tìm thấy lần chi này.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    }
    let Some(relative) = store::get_receipt_photo(&state.pool, &rid, &spend_id).await? else {
        return Err(AppError::new(
            "NOT_FOUND",
            "Giao dịch này chưa có ảnh biên lai.",
            StatusCode::NOT_FOUND,
        )
        .with_request_id(&rid));
    };
    let safe = relative.replace('\\', "/");
    if safe.contains("..") {
        return Err(crate::store::internal_debug(&rid, "receipt path lạ"));
    }
    let full = receipts_dir(&state.settings).parent().unwrap_or(std::path::Path::new(".")).join(&safe);
    let bytes = tokio::fs::read(&full)
        .await
        .map_err(|_| {
            AppError::new(
                "NOT_FOUND",
                "Giao dịch này chưa có ảnh biên lai.",
                StatusCode::NOT_FOUND,
            )
            .with_request_id(&rid)
        })?;
    let mime = match full.extension().and_then(|e| e.to_str()) {
        Some("png") => "image/png",
        Some("webp") => "image/webp",
        _ => "image/jpeg",
    };
    Ok((
        [(axum::http::header::CONTENT_TYPE, mime)],
        bytes,
    )
        .into_response())
}
