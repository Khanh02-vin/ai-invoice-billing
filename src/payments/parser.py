"""Parse tin nhắn/email thông báo giao dịch -> PaymentEvent.

Một parser dùng chung cho mọi nguồn: SMS ngân hàng (VCB, ACB, TCB, MB...),
email alert, ví MoMo / ZaloPay / VNPay. Caller đẩy text thô vào, nguồn nào
đọc được số tiền thì ra PaymentEvent; không đọc được trả None.

Chiến lược:
1. Ưu tiên số có đơn vị tiền (VND/đ/$...) — bỏ qua số đứng sau từ khóa số dư
   (SD/số dư/balance) vì đó là số dư MỐI, không phải số tiền giao dịch.
2. Nếu không có, tìm số đứng sau từ khóa giao dịch (trừ/thanh toán/GD/transfer...).
3. Direction: có dấu trừ hoặc từ khóa chi -> debit; từ khóa credit -> credit;
   mặc định debit (phần lớn alert là chi tiêu).
"""
import hashlib
import re
from datetime import datetime
from typing import Optional

from ..domain.payments import PaymentEvent, TxDirection
from ..extract.extractor import _parse_number

# Số + đơn vị tiền. Thanh hai dạng: "350.000 VND" / "$250.00".
_AMOUNT_RE = re.compile(
    r"(?P<minus>[-−])?\s*(?P<num>\d[\d.,]*)\s*"
    r"(?P<unit>VN[ĐD]|đ|₫|USD|EUR|GBP|JPY|AUD|THB|RM|[$€£])"
    r"|(?P<pre>[$€£])\s*(?P<num_pre>\d[\d.,]+)",
    re.I,
)

# Fallback: số đứng sau từ khóa giao dịch (khi SMS không ghi đơn vị tiền)
_KEYWORD_RE = re.compile(
    r"(?:trừ|trú|tru|thanh\s*toán|thanh\s*toan|chuyển|chuyen|chi\s*tiêu|chi\s*tieu|"
    r"mua|gửi|gui|GD|payment|paid|debit|transfer|spent|purchase)\s*[:=]?\s*"
    r"(?P<minus>[-−])?\s*(?P<num>\d[\d.,]{2,})",
    re.I,
)

# Từ khóa báo "số này là số dư" — áp cho vùng ~30 ký tự đứng trước con số
_BALANCE_RE = re.compile(
    r"(?:SD|số\s*dư|so\s*dư|sodu|balance|remaining|còn\s*lại|con\s*lai|available|"
    r"số\s*dư\s*khả\s*dùng)\s*[:=]?\s*$",
    re.I,
)

_CREDIT_RE = re.compile(
    r"(?:cộng\s*tiền|cong\s*tien|đã\s*cộng|da\s*cong|"
    r"nhận\s*tiền|nhan\s*tien|đã\s*nhận|da\s*nhan|"
    r"vào\s*tài\s*khoản|vao\s*tai\s*khoan|"
    r"received|credit|refund|hoàn\s*tiền|hoan\s*tien)",
    re.I,
)
_DEBIT_RE = re.compile(
    r"(?:trừ|trú|tru|thanh\s*toán|thanh\s*toan|chi\s*tiêu|chi\s*tieu|chuyển\s*ra|"
    r"chuyen\s*ra|debit|paid|spent|purchase|mua\s*hàng|mua\s*hang)",
    re.I,
)

# Merchant: ưu tiên nhãn có sẵn (GDV/ND/nội dung), sau đó "tại/at"
_MERCHANT_RES = (
    re.compile(r"GDV\s*[:=]?\s*([A-Za-z0-9][^,;\n]{1,50})", re.I),
    re.compile(r"(?:\bND\b|nội\s*dung|noi\s*dung|description|memo)\s*[:=]?\s*([^,;\n]{2,50})", re.I),
    re.compile(
        r"\b(?:at|tại|tai|thanh\s*toán\s*tại|thanh\s*toan\s*tai|mua\s*tại|mua\s*tai|"
        r"cửa\s*hàng|cua\s*hang)\s+([A-Za-z0-9À-ỹ][^,;\n]{1,45})",
        re.I,
    ),
)

# Đuôi bị nhóm merchant nuốt oan khi các câu dính liền nhau:
# "GDV:VINMART QUAN 1. SD: 15,588,004" -> merchant phải dừng trước "SD".
_MERCHANT_STOP_RE = re.compile(
    r"[.\-|]?\s*(?:SD|s[ốo]\s*d[ưu]|balance|s[ốo]\s*d[ưu]\s*kh[ảa]\s*d[ụu]ng|"
    r"l[úu]c|v[àa]o\s*l[úu]c|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    re.I,
)

# Ngày đầy đủ có năm: 01/10/2026 14:33 | 2026-10-01
_DATE_RE = re.compile(
    r"(?P<d>\d{1,2})[/-](?P<m>\d{1,2})[/-](?P<y>\d{2,4})(?:[ T](?P<h>\d{1,2}):(?P<min>\d{2}))?"
    r"|(?P<y2>\d{4})-(?P<m2>\d{1,2})-(?P<d2>\d{1,2})(?:[ T](?P<h2>\d{1,2}):(?P<min2>\d{2}))?"
)

_UNIT_CURRENCY = {
    "đ": "VND", "₫": "VND", "vnd": "VND", "vnđ": "VND",
    "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP", "jpy": "JPY", "aud": "AUD",
    "thb": "THB", "rm": "MYR",
}


def _currency(unit: str) -> str:
    return _UNIT_CURRENCY.get(unit.lower(), unit.upper())


def _parse_when(text: str) -> Optional[str]:
    """Đọc ngày/giờ đầu tiên trong text -> ISO string (None nếu thiếu năm)."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        if m.group("y"):
            y = int(m.group("y"))
            y = y + 2000 if y < 100 else y
            d, mo = int(m.group("d")), int(m.group("m"))
            hh, mi = int(m.group("h") or 0), int(m.group("min") or 0)
        else:
            y, mo, d = int(m.group("y2")), int(m.group("m2")), int(m.group("d2"))
            hh, mi = int(m.group("h2") or 0), int(m.group("min2") or 0)
        return datetime(y, mo, d, hh, mi).isoformat()
    except ValueError:
        return None


def _pick_merchant(text: str) -> str:
    for rx in _MERCHANT_RES:
        m = rx.search(text)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .:;,-")
            # Cắt các đuôi oan nuốt sau "GDV:" khi các trường cùng dòng,
            # ví dụ "VINMART QUAN 1. SD: 15,588,004" -> "VINMART QUAN 1"
            stop = _MERCHANT_STOP_RE.search(name)
            if stop and stop.start() > 0:  # giữ nguyên nếu tên bắt đầu bằng mốc dừng
                name = name[: stop.start()].rstrip(" .:;,-")
            if name:
                return name[:60]
    return ""


def _direction(ctx: str, minus: bool) -> TxDirection:
    if minus:
        return TxDirection.DEBIT
    if _CREDIT_RE.search(ctx):
        return TxDirection.CREDIT
    if _DEBIT_RE.search(ctx):
        return TxDirection.DEBIT
    return TxDirection.DEBIT  # mặc định: alert bank phần lớn là chi tiền


def _valid_amount(num_str: str, value: float) -> bool:
    """Loại số điện thoại (bắt đầu bằng 0...) và số vô nghĩa."""
    if num_str.startswith("0") and len(num_str) > 1:
        return False
    return 1.0 <= value <= 1e15


def parse_payment_text(text: str, source: str = "sms") -> Optional[PaymentEvent]:
    """Parse text thô thông báo giao dịch. Trả None nếu không đọc được số tiền."""
    text = (text or "").strip()
    if not text:
        return None

    # 1) Ưu tiên số có đơn vị tiền — bỏ qua số đứng sau từ khóa số dư
    amount = currency = None
    minus = False
    ctx = ""
    for m in _AMOUNT_RE.finditer(text):
        before = text[max(0, m.start() - 30):m.start()]
        if _BALANCE_RE.search(before):
            continue
        num_str = m.group("num") or m.group("num_pre") or ""
        try:
            value = _parse_number(num_str)
        except ValueError:
            continue
        if not _valid_amount(num_str, value):
            continue
        amount = value
        currency = _currency(m.group("unit") or m.group("pre"))
        minus = bool(m.group("minus"))
        ctx = before
        break

    # 2) Fallback: số sau từ khóa giao dịch (VD "GD -2.000.000" không có đơn vị)
    if amount is None:
        for m in _KEYWORD_RE.finditer(text):
            before = text[max(0, m.start() - 30):m.start()]
            if _BALANCE_RE.search(before):
                continue
            num_str = m.group("num")
            try:
                value = _parse_number(num_str)
            except ValueError:
                continue
            if not _valid_amount(num_str, value):
                continue
            amount, currency, minus, ctx = value, "VND", bool(m.group("minus")), before
            break

    if amount is None:
        return None

    return PaymentEvent(
        amount=amount,
        currency=currency,
        direction=_direction(ctx, minus),
        merchant=_pick_merchant(text),
        occurred_at=_parse_when(text),
        source=source,
    )


def derive_ref(raw_text: str, external_ref: str = "") -> str:
    """Dedupe key: dùng ref từ nguồn nếu có, không thì hash nội dung."""
    if external_ref:
        return external_ref
    return hashlib.sha1(raw_text.strip().encode("utf-8")).hexdigest()[:16]
