//! Parse body JSON thủ công để khớp contract 422 của FastAPI/Pydantic:
//! `{"error":{"code":"VALIDATION_ERROR","details":{"fields":[["body","amount"]]}}}`.
//!
//! Pydantic lax mode: số nguyên chấp nhận JSON number (phần lẻ = 0) hoặc chuỗi
//! chữ số; bool không phải int. Độ dài đếm theo số ký tự Unicode.

use serde_json::{json, Map, Value};

use crate::error::AppError;
use crate::timefmt;

pub fn push_field(fields: &mut Vec<Value>, key: &str) {
    fields.push(json!(["body", key]));
}

pub fn push_query_field(fields: &mut Vec<Value>, key: &str) {
    fields.push(json!(["query", key]));
}

/// Body phải là JSON object. Trả None (đã push lỗi) nếu không đúng.
pub fn body_object<'a>(
    body: &'a Value,
    fields: &mut Vec<Value>,
) -> Option<&'a Map<String, Value>> {
    match body {
        Value::Object(map) => Some(map),
        _ => {
            fields.push(json!(["body"]));
            None
        }
    }
}

/// Parse raw bytes → Value; JSON hỏng → lỗi loc ["body"].
pub fn parse_body(bytes: &[u8], fields: &mut Vec<Value>) -> Option<Value> {
    match serde_json::from_slice(bytes) {
        Ok(v) => Some(v),
        Err(_) => {
            fields.push(json!(["body"]));
            None
        }
    }
}

/// Nếu đã có lỗi tích luỹ → trả AppError::validation.
pub fn finish(fields: Vec<Value>) -> Result<(), AppError> {
    if fields.is_empty() {
        Ok(())
    } else {
        Err(AppError::validation(fields))
    }
}

/// str bắt buộc (không default của Pydantic): thiếu/null/sai type → lỗi.
pub fn req_str_field(obj: &Map<String, Value>, key: &str, fields: &mut Vec<Value>) -> String {
    match obj.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => {
            push_field(fields, key);
            String::new()
        }
    }
}

/// str không-optional: thiếu → default; null hoặc sai type → lỗi.
pub fn str_field(
    obj: &Map<String, Value>,
    key: &str,
    default: &str,
    fields: &mut Vec<Value>,
) -> String {
    match obj.get(key) {
        None => default.to_owned(),
        Some(Value::String(s)) => s.clone(),
        Some(_) => {
            push_field(fields, key);
            default.to_owned()
        }
    }
}

/// str | None: thiếu hoặc null → None; sai type → lỗi.
pub fn opt_str_field(
    obj: &Map<String, Value>,
    key: &str,
    fields: &mut Vec<Value>,
) -> Option<String> {
    match obj.get(key) {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(_) => {
            push_field(fields, key);
            None
        }
    }
}

/// Số nguyên kiểu lax của Pydantic.
pub fn int_lax(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(_) => None,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else {
                n.as_f64().filter(|f| f.fract() == 0.0 && f.is_finite()).map(|f| f as i64)
            }
        }
        Value::String(s) => s.parse().ok(),
        _ => None,
    }
}

/// int bắt buộc với khoảng [min, max] (gt=0, ge=1, le=… của Pydantic).
pub fn int_field(
    obj: &Map<String, Value>,
    key: &str,
    min: i64,
    max: i64,
    fields: &mut Vec<Value>,
) -> Option<i64> {
    let value = match obj.get(key) {
        None | Some(Value::Null) => {
            push_field(fields, key);
            return None;
        }
        Some(v) => v,
    };
    match int_lax(value) {
        Some(n) if (min..=max).contains(&n) => Some(n),
        _ => {
            push_field(fields, key);
            None
        }
    }
}

/// int có default (party_size = 1): thiếu → default; có mặt phải hợp lệ.
pub fn int_field_default(
    obj: &Map<String, Value>,
    key: &str,
    default: i64,
    min: i64,
    max: i64,
    fields: &mut Vec<Value>,
) -> i64 {
    match obj.get(key) {
        None | Some(Value::Null) => default,
        Some(v) => match int_lax(v) {
            Some(n) if (min..=max).contains(&n) => n,
            _ => {
                push_field(fields, key);
                default
            }
        },
    }
}

/// float | None (lat/lon): lax — number hoặc chuỗi số.
pub fn opt_float_field(
    obj: &Map<String, Value>,
    key: &str,
    fields: &mut Vec<Value>,
) -> Option<f64> {
    match obj.get(key) {
        None | Some(Value::Null) => None,
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => match s.trim().parse() {
            Ok(f) => Some(f),
            Err(_) => {
                push_field(fields, key);
                None
            }
        },
        Some(_) => {
            push_field(fields, key);
            None
        }
    }
}

/// datetime | None → chuỗi lưu DB; sai định dạng → lỗi.
pub fn opt_datetime_field(
    obj: &Map<String, Value>,
    key: &str,
    fields: &mut Vec<Value>,
) -> Option<String> {
    match obj.get(key) {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => match timefmt::parse_client_datetime(s) {
            Some(stored) => Some(stored),
            None => {
                push_field(fields, key);
                None
            }
        },
        Some(_) => {
            push_field(fields, key);
            None
        }
    }
}

/// Độ dài chuỗi theo số ký tự — sai khoảng → lỗi.
pub fn check_len(value: &str, key: &str, min: usize, max: usize, fields: &mut Vec<Value>) {
    let n = value.chars().count();
    if n < min || n > max {
        push_field(fields, key);
    }
}

// ---- Query params (FastAPI Query ge/le → 422 loc ["query", key]) ----

pub fn query_int(
    params: &std::collections::HashMap<String, String>,
    key: &str,
    min: i64,
    max: i64,
    fields: &mut Vec<Value>,
) -> Option<i64> {
    match params.get(key) {
        None => None,
        Some(raw) => match raw.trim().parse::<i64>() {
            Ok(v) if (min..=max).contains(&v) => Some(v),
            _ => {
                push_query_field(fields, key);
                None
            }
        },
    }
}

pub fn query_int_default(
    params: &std::collections::HashMap<String, String>,
    key: &str,
    default: i64,
    min: i64,
    max: i64,
    fields: &mut Vec<Value>,
) -> i64 {
    match params.get(key) {
        None => default,
        Some(raw) => match raw.trim().parse::<i64>() {
            Ok(v) if (min..=max).contains(&v) => v,
            _ => {
                push_query_field(fields, key);
                default
            }
        },
    }
}

/// float có ge/le (lat/lon) — thiếu → None, sai định dạng hoặc ngoài khoảng → lỗi.
pub fn query_float(
    params: &std::collections::HashMap<String, String>,
    key: &str,
    min: f64,
    max: f64,
    fields: &mut Vec<Value>,
) -> Option<f64> {
    match params.get(key) {
        None => None,
        Some(raw) => match raw.trim().parse::<f64>() {
            Ok(v) if (min..=max).contains(&v) => Some(v),
            _ => {
                push_query_field(fields, key);
                None
            }
        },
    }
}

/// float BẮT BUỘC (FastAPI `lat: float = Query(ge, le)` không có default) —
/// thiếu hoặc sai → lỗi 422.
pub fn query_float_req(
    params: &std::collections::HashMap<String, String>,
    key: &str,
    min: f64,
    max: f64,
    fields: &mut Vec<Value>,
) -> f64 {
    match params.get(key) {
        None => {
            push_query_field(fields, key);
            0.0
        }
        Some(raw) => match raw.trim().parse::<f64>() {
            Ok(v) if (min..=max).contains(&v) => v,
            _ => {
                push_query_field(fields, key);
                0.0
            }
        },
    }
}

/// float với ràng buộc gt (không tính min) — dùng cho radius_km (gt=0, le=50).
/// Thiếu → None (caller lấy default); sai/ngoài khoảng → lỗi.
pub fn query_float_gt(
    params: &std::collections::HashMap<String, String>,
    key: &str,
    gt_min: f64,
    max: f64,
    fields: &mut Vec<Value>,
) -> Option<f64> {
    match params.get(key) {
        None => None,
        Some(raw) => match raw.trim().parse::<f64>() {
            Ok(v) if v > gt_min && v <= max => Some(v),
            _ => {
                push_query_field(fields, key);
                None
            }
        },
    }
}
