"""Đọc số tiền từ nội dung QR.

Nhận bốn dạng thường gặp: JSON, URL có query, chuỗi số thuần, và VietQR/EMVCo
(dạng TLV in trên bill chuyển khoản của ngân hàng Việt Nam). Không hiểu gì thì trả
``None`` để UI bắt người dùng tự nhập — chưa bao giờ đoán bừa ra một con số.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

MIN_AMOUNT = 1_000
MAX_AMOUNT = 100_000_000

AMOUNT_KEYS = {
    "amount",
    "sotien",
    "tongtien",
    "tong",
    "total",
    "totalamount",
    "price",
    "tien",
    "giatri",
}
PARTY_KEYS = {"peoplesize", "partysize", "songuoi", "people", "guests", "quantity", "soluong"}
MERCHANT_KEYS = {"merchantname", "merchant", "shopname", "shop", "storename", "tenquan", "name"}


def _norm_key(key: object) -> str:
    return str(key).strip().casefold().replace("_", "").replace("-", "").replace(" ", "")


def _to_int(value: object) -> int | None:
    """``"125.000đ"``, ``"125k"``, ``125000`` → ``125000``. Ngoài khoảng hợp lý → None."""
    multiplier = 1
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    elif isinstance(value, str):
        text = value.strip().casefold()
        for junk in ("vnđ", "vnd", "₫", "đ", "\u00a0", " "):
            text = text.replace(junk, "")
        if text.endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        text = text.replace(".", "").replace(",", "")
        if not text.isdigit():
            return None
        number = int(text) * multiplier
    else:
        return None
    if not MIN_AMOUNT <= number <= MAX_AMOUNT:
        return None
    return number


def _to_count(value: object) -> int | None:
    """Số người là số nhỏ — không dùng chung ngưỡng tiền với ``_to_int``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first(data: dict, keys: set[str]) -> object | None:
    for key, value in data.items():
        if _norm_key(key) in keys:
            return value
    return None


def _from_json(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _from_query(raw: str) -> dict | None:
    if "?" not in raw:
        return None
    query = urlsplit(raw).query
    if not query:
        return None
    return {key: values[-1] for key, values in parse_qs(query).items() if values}


def _tlv_fields(text: str) -> dict[str, str]:
    """Tách chuỗi EMVCo: 2 số tag + 2 số độ dài + giá trị, lặp đến hết."""
    fields: dict[str, str] = {}
    index = 0
    while index + 4 <= len(text):
        tag, length = text[index : index + 2], text[index + 2 : index + 4]
        if not (tag.isdigit() and length.isdigit()):
            break
        size = int(length)
        value = text[index + 4 : index + 4 + size]
        if len(value) != size:
            break
        fields.setdefault(tag, value)
        index += 4 + size
    return fields


def _emv_amount(value: str | None) -> int | None:
    """Tag 54: ``"125000"`` hoặc ``"125000.00"`` — không đọc hàng nghìn kiểu Việt Nam."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        number = int(text)
    elif re.fullmatch(r"\d+\.\d{1,2}", text):
        number = int(round(float(text)))
    else:
        return None
    if not MIN_AMOUNT <= number <= MAX_AMOUNT:
        return None
    return number


def _from_emv(raw: str) -> tuple[int | None, str | None] | None:
    """VietQR/EMVCo → (số tiền, tên đơn vị nhận tiền).

    Chỉ nhận khi có tag 53 (mã tiền tệ) — chuỗi TLV khác không phải QR thanh toán.
    QR tĩnh không có tag 54: trả số tiền ``None`` nhưng vẫn giữ tên quán.
    """
    fields = _tlv_fields(raw)
    if not fields.get("53"):
        return None
    merchant = (fields.get("59") or "").strip() or None
    return _emv_amount(fields.get("54")), merchant


def _emv_bank_account(raw: str) -> tuple[str | None, str | None]:
    """Tag 38 → (BIN ngân hàng 6 số, số tài khoản người nhận).

    Chỉ nhận GUI NAPAS ``A000000727``. Tag 38 của ví khác cũng là TLV nhưng đánh
    số khác — đọc bừa thành công thức chuyển khoản là chuyển nhầm người.
    """
    nested = _tlv_fields(_tlv_fields(raw).get("38") or "")
    if nested.get("00") != "A000000727":
        return None, None
    inner = _tlv_fields(nested.get("01") or "")
    bank_bin = (inner.get("00") or "").strip()
    account = (inner.get("01") or "").strip()
    if not (len(bank_bin) == 6 and bank_bin.isdigit()):
        return None, None
    if not re.fullmatch(r"[0-9A-Za-z]{4,32}", account):
        return None, None
    return bank_bin, account


def parse_qr_text(text: str) -> dict:
    """``{"amount", "party_size", "merchant_name", "bank_bin", "account", "raw"}``
    — không bao giờ ném lỗi."""
    raw = (text or "").strip()
    amount: int | None = None
    party_size: int | None = None
    merchant_name: str | None = None
    bank_bin: str | None = None
    account: str | None = None

    data = _from_json(raw) or _from_query(raw)
    if data:
        amount = _to_int(_first(data, AMOUNT_KEYS))
        party_size = _to_count(_first(data, PARTY_KEYS))
        name = _first(data, MERCHANT_KEYS)
        merchant_name = (str(name).strip() if name is not None else "") or None

    emv = _from_emv(raw)
    if emv is not None:
        emv_amount, emv_merchant = emv
        if amount is None:
            amount = emv_amount
        if merchant_name is None:
            merchant_name = emv_merchant
        bank_bin, account = _emv_bank_account(raw)

    if amount is None:
        amount = _to_int(raw)

    if party_size is not None and not 1 <= party_size <= 50:
        party_size = None
    return {
        "amount": amount,
        "party_size": party_size,
        "merchant_name": merchant_name,
        "bank_bin": bank_bin,
        "account": account,
        "raw": raw,
    }
