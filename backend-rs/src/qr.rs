//! Đọc số tiền từ nội dung QR — port 1:1 của src/qr.py.
//!
//! Bốn dạng: JSON, URL query, chuỗi số thuần, VietQR/EMVCo (TLV). Không hiểu thì
//! trả None — chưa bao giờ đoán bừa ra một con số. Các test dưới đây dùng đúng
//! vector do bản Python sinh ra (parity check đa ngôn ngữ).

use serde_json::{Map, Value};
use std::collections::HashMap;

const MIN_AMOUNT: i64 = 1_000;
const MAX_AMOUNT: i64 = 100_000_000;

const AMOUNT_KEYS: [&str; 9] = [
    "amount", "sotien", "tongtien", "tong", "total", "totalamount", "price", "tien", "giatri",
];
const PARTY_KEYS: [&str; 7] = [
    "peoplesize", "partysize", "songuoi", "people", "guests", "quantity", "soluong",
];
const MERCHANT_KEYS: [&str; 7] = [
    "merchantname", "merchant", "shopname", "shop", "storename", "tenquan", "name",
];

#[derive(Debug, PartialEq)]
pub struct QrParse {
    pub amount: Option<i64>,
    pub party_size: Option<i64>,
    pub merchant_name: Option<String>,
    pub bank_bin: Option<String>,
    pub account: Option<String>,
    pub raw: String,
}

fn is_ascii_digits(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_digit())
}

/// Python: str.strip().casefold().replace("_","").replace("-","").replace(" ","")
fn norm_key(key: &str) -> String {
    key.trim()
        .to_lowercase()
        .chars()
        .filter(|c| *c != '_' && *c != '-' && *c != ' ')
        .collect()
}

/// ``"125.000đ"``, ``"125k"``, ``125000`` → ``125000``. Ngoài khoảng hợp lý → None.
/// Lưu ý khớp Python: int(float) cắt phần thập phân (không làm tròn), và
/// `"125000.50"` → bỏ dấu chấm → `12500050`.
fn to_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(_) => None,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else {
                // int(float) của Python cắt về 0
                n.as_f64().map(|f| f.trunc() as i64)
            }
        }
        Value::String(s) => to_int_str(s),
        _ => None,
    }
}

fn to_int_str(raw: &str) -> Option<i64> {
    let mut text = raw.trim().to_lowercase();
    for junk in ["vnđ", "vnd", "₫", "đ", "\u{00a0}", " "] {
        text = text.replace(junk, "");
    }
    let mut multiplier: i64 = 1;
    if text.ends_with('k') {
        multiplier = 1_000;
        text.truncate(text.len() - 1);
    }
    text = text.replace('.', "").replace(',', "");
    if !is_ascii_digits(&text) {
        return None;
    }
    let number: i64 = text.parse().ok()?;
    let number = number.checked_mul(multiplier)?;
    if !(MIN_AMOUNT..=MAX_AMOUNT).contains(&number) {
        return None;
    }
    Some(number)
}

/// Số người là số nhỏ — không dùng chung ngưỡng tiền với ``to_int``.
fn to_count(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(_) => None,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else {
                n.as_f64().map(|f| f.trunc() as i64)
            }
        }
        Value::String(s) => {
            let t = s.trim();
            if is_ascii_digits(t) {
                t.parse().ok()
            } else {
                None
            }
        }
        _ => None,
    }
}

/// Key đầu tiên (theo thứ tự chèn — serde_json preserve_order khớp dict Python)
/// có tên chuẩn hoá nằm trong danh sách.
fn first<'a>(data: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    data.iter()
        .find(|(key, _)| keys.contains(&norm_key(key).as_str()))
        .map(|(_, v)| v)
}

fn from_json(raw: &str) -> Option<Map<String, Value>> {
    match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(map)) => Some(map),
        _ => None,
    }
}

/// unquote_plus của Python: '+' → khoảng trắng rồi percent-decode (lỗi → thay thế).
fn unquote_plus(s: &str) -> String {
    let with_space = s.replace('+', " ");
    percent_encoding::percent_decode_str(&with_space)
        .decode_utf8_lossy()
        .into_owned()
}

/// urlsplit(raw).query + parse_qs (mặc định: bỏ cặp rỗng, giữ giá trị cuối).
fn from_query(raw: &str) -> Option<Map<String, Value>> {
    // urlsplit cắt fragment '#' TRƯỚC khi tìm '?'
    let base = raw.split('#').next().unwrap_or("");
    let idx = base.find('?')?;
    let query = &base[idx + 1..];
    if query.is_empty() {
        return None;
    }
    let mut out = Map::new();
    for pair in query.split('&') {
        let Some(eq) = pair.find('=') else { continue };
        let key = unquote_plus(&pair[..eq]);
        let val = unquote_plus(&pair[eq + 1..]);
        if val.is_empty() {
            continue; // keep_blank_values=False
        }
        out.insert(key, Value::String(val)); // last wins
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// Tách chuỗi EMVCo: 2 số tag + 2 số độ dài + giá trị, lặp đến hết.
/// Tag trùng: giá trị ĐẦU tiên thắng (setdefault).
fn tlv_fields(text: &str) -> HashMap<String, String> {
    let chars: Vec<char> = text.chars().collect();
    let mut fields = HashMap::new();
    let mut index = 0usize;
    while index + 4 <= chars.len() {
        let tag: String = chars[index..index + 2].iter().collect();
        let length: String = chars[index + 2..index + 4].iter().collect();
        if !is_ascii_digits(&tag) || !is_ascii_digits(&length) {
            break;
        }
        let Ok(size) = length.parse::<usize>() else { break };
        if index + 4 + size > chars.len() {
            break;
        }
        let value: String = chars[index + 4..index + 4 + size].iter().collect();
        fields.entry(tag).or_insert(value);
        index += 4 + size;
    }
    fields
}

/// Tag 54: ``"125000"`` hoặc ``"125000.00"`` — không đọc hàng nghìn kiểu Việt Nam.
/// round() của Python là banker's rounding → round_ties_even.
fn emv_amount(value: Option<&str>) -> Option<i64> {
    let text = value?.trim();
    let number = if is_ascii_digits(text) {
        text.parse::<i64>().ok()?
    } else if is_decimal_1_or_2(text) {
        let f: f64 = text.parse().ok()?;
        let rounded = f.round_ties_even();
        if !rounded.is_finite() {
            return None;
        }
        rounded as i64
    } else {
        return None;
    };
    if !(MIN_AMOUNT..=MAX_AMOUNT).contains(&number) {
        return None;
    }
    Some(number)
}

/// regex \d+\.\d{1,2}
fn is_decimal_1_or_2(text: &str) -> bool {
    match text.split_once('.') {
        Some((left, right)) => {
            is_ascii_digits(left)
                && (1..=2).contains(&right.chars().count())
                && right.chars().all(|c| c.is_ascii_digit())
        }
        None => false,
    }
}

/// VietQR/EMVCo → (số tiền, tên đơn vị nhận tiền).
/// Chỉ nhận khi có tag 53 (mã tiền tệ) — chuỗi TLV khác không phải QR thanh toán.
/// QR tĩnh không có tag 54: trả số tiền None nhưng vẫn giữ tên quán.
fn from_emv(raw: &str) -> Option<(Option<i64>, Option<String>)> {
    let fields = tlv_fields(raw);
    match fields.get("53") {
        None => None,
        Some(v) if v.is_empty() => None,
        Some(_) => {
            let merchant = fields
                .get("59")
                .map(|s| s.trim().to_owned())
                .filter(|s| !s.is_empty());
            let amount = emv_amount(fields.get("54").map(String::as_str));
            Some((amount, merchant))
        }
    }
}

/// Tag 38 → (BIN ngân hàng 6 số, số tài khoản người nhận).
/// Chỉ nhận GUI NAPAS ``A000000727``. Tag 38 của ví khác cũng là TLV nhưng đánh
/// số khác — đọc bừa thành công thức chuyển khoản là chuyển nhầm người.
fn emv_bank_account(raw: &str) -> (Option<String>, Option<String>) {
    let fields = tlv_fields(raw);
    let nested = tlv_fields(fields.get("38").map(String::as_str).unwrap_or(""));
    if nested.get("00").map(String::as_str) != Some("A000000727") {
        return (None, None);
    }
    let inner = tlv_fields(nested.get("01").map(String::as_str).unwrap_or(""));
    let bank_bin = inner
        .get("00")
        .map(|s| s.trim().to_owned())
        .unwrap_or_default();
    let account = inner
        .get("01")
        .map(|s| s.trim().to_owned())
        .unwrap_or_default();
    if !(bank_bin.len() == 6 && bank_bin.chars().all(|c| c.is_ascii_digit())) {
        return (None, None);
    }
    let account_ok = (4..=32).contains(&account.chars().count())
        && account.chars().all(|c| c.is_ascii_alphanumeric());
    if !account_ok {
        return (None, None);
    }
    (Some(bank_bin), Some(account))
}

/// ``{"amount", "party_size", "merchant_name", "bank_bin", "account", "raw"}``
/// — không bao giờ ném lỗi.
pub fn parse_qr_text(text: &str) -> QrParse {
    let raw = text.trim();
    let mut amount: Option<i64> = None;
    let mut party_size: Option<i64> = None;
    let mut merchant_name: Option<String> = None;
    let mut bank_bin: Option<String> = None;
    let mut account: Option<String> = None;

    let data = from_json(raw).or_else(|| from_query(raw));
    if let Some(data) = &data {
        amount = first(data, &AMOUNT_KEYS).and_then(to_int);
        party_size = first(data, &PARTY_KEYS).and_then(to_count);
        merchant_name = first(data, &MERCHANT_KEYS).and_then(|v| match v {
            Value::Null => None,
            Value::String(s) => {
                let t = s.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(t.to_owned())
                }
            }
            Value::Bool(b) => Some(if *b { "True" } else { "False" }.to_owned()),
            Value::Number(n) => Some(n.to_string()),
            // dict/list: Python dùng str(repr) — QR thật không có dạng này, coi như None
            _ => None,
        });
    }

    if let Some((emv_amt, emv_merchant)) = from_emv(raw) {
        if amount.is_none() {
            amount = emv_amt;
        }
        if merchant_name.is_none() {
            merchant_name = emv_merchant;
        }
        let (bin, acc) = emv_bank_account(raw);
        bank_bin = bin;
        account = acc;
    }

    if amount.is_none() {
        amount = to_int_str(raw);
    }

    if let Some(p) = party_size {
        if !(1..=50).contains(&p) {
            party_size = None;
        }
    }

    QrParse {
        amount,
        party_size,
        merchant_name,
        bank_bin,
        account,
        raw: raw.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vector do parse_qr_text của Python sinh ra — phải khớp từng giá trị.
    fn py_case(text: &str, amount: Option<i64>, party: Option<i64>, merchant: Option<&str>) {
        let got = parse_qr_text(text);
        assert_eq!(got.amount, amount, "amount cho {text:?}");
        assert_eq!(got.party_size, party, "party_size cho {text:?}");
        assert_eq!(got.merchant_name, merchant.map(str::to_owned), "merchant cho {text:?}");
        assert_eq!(got.raw, text.trim(), "raw cho {text:?}");
    }

    #[test]
    fn parity_with_python_vectors() {
        py_case("", None, None, None);
        py_case("125000", Some(125000), None, None);
        py_case("125.000đ", Some(125000), None, None); // bỏ dấu chấm
        py_case("125k", Some(125000), None, None);
        py_case("999", None, None, None); // dưới ngưỡng 1.000
        py_case(
            "{\"amount\": 45000, \"soluong\": 3}",
            Some(45000),
            Some(3),
            None,
        );
        // Quirk Python: "125000.50" bỏ dấu chấm → 12500050 (không phải 125000.5 VND)
        py_case(
            "{\"totalAmount\": \"125000.50\", \"tenquan\": \"Cơm Tám\"}",
            Some(12500050),
            None,
            Some("Cơm Tám"),
        );
        py_case(
            "https://vietqr.vn/pay?amount=125000&shop=Quan+An&songuoi=2",
            Some(125000),
            Some(2),
            Some("Quan An"),
        );
        // TLV desync (tag 26 dài hơn dữ liệu thật) → null toàn bộ, như Python
        py_case(
            "00020101021226660014A0000006270006930004010212345678530370454051250005802VN5909QUAN TEST6008HANOI62070503ABC6304ABCD",
            None, None, None,
        );
        py_case(
            "00020101021126360010A0000007270110069354373401020000103054103310102069820408069354373453037045802VN5907VINCOMS6010HA NOI62130317050806935437346304C1A3",
            None, None, Some("VINCOMS"),
        );
        py_case(
            "00020101021138540010A00000072701110006935437340102VQRQAFREE0103STM0203VQB0306982437045103037045802VN5904CITI6010HANOI6304AB12",
            None, None, None,
        );
    }

    #[test]
    fn wellformed_napas_gui() {
        // GUI NAPAS hợp lệ: BIN 693543, TK 12345678, amount 125000
        let qr = "00020101021138400010A00000072701220006693543010812345678530370454061250005802VN5909QUAN TEST6304ABCD";
        let got = parse_qr_text(qr);
        assert_eq!(got.amount, Some(125000));
        assert_eq!(got.bank_bin.as_deref(), Some("693543"));
        assert_eq!(got.account.as_deref(), Some("12345678"));
        assert_eq!(got.merchant_name.as_deref(), Some("QUAN TEST"));
    }

    #[test]
    fn static_vietqr_has_no_amount() {
        // QR tĩnh: có tag 53 nhưng không có tag 54 → amount None, merchant vẫn có
        let qr = "00020101021153037045802VN5909QUAN TEST6304ABCD";
        let got = parse_qr_text(qr);
        assert_eq!(got.amount, None);
        assert_eq!(got.merchant_name.as_deref(), Some("QUAN TEST"));
    }

    #[test]
    fn non_napas_gui_never_yields_account() {
        // Tag 38 GUI khác (không phải A000000727) → không đọc BIN/TK, nhưng
        // tag 53/54 ở ngoài vẫn đọc được amount (như Python)
        let qr = "00020101021138400010A00000061501220006693543010812345678530370454061250005802VN6304ABCD";
        let got = parse_qr_text(qr);
        assert_eq!(got.amount, Some(125000));
        assert_eq!(got.bank_bin, None);
        assert_eq!(got.account, None);
    }

    #[test]
    fn emv_amount_decimal_bankers_rounding() {
        // round() Python: .5 về số chẵn gần nhất
        assert_eq!(emv_amount(Some("125000.00")), Some(125000));
        assert_eq!(emv_amount(Some("999")), None); // dưới ngưỡng
        assert_eq!(emv_amount(Some("125.0.0")), None);
        assert_eq!(emv_amount(Some("")), None);
        assert_eq!(emv_amount(None), None);
        assert_eq!(emv_amount(Some("100000001")), None); // trên ngưỡng
    }

    #[test]
    fn party_size_out_of_range_dropped() {
        let got = parse_qr_text("{\"soluong\": 99}");
        assert_eq!(got.party_size, None);
        let got = parse_qr_text("{\"soluong\": 50}");
        assert_eq!(got.party_size, Some(50));
    }

    #[test]
    fn json_with_emv_tail_parses_nothing() {
        // Python: raw có JSON phía trước + đuôi TLV → from_json lỗi (rác phía sau),
        // TLV break ngay '{' → tất cả null. Không bao giờ "nối" hai nửa.
        let text = "{\"tenquan\": \"Quán A\"}000201010211530370454061250005802VN6304ABCD";
        let got = parse_qr_text(text);
        assert_eq!(got.amount, None);
        assert_eq!(got.merchant_name, None);
    }
}
