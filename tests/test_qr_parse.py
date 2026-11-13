"""Đọc số tiền/số người từ QR — nhận 3 dạng, không hiểu thì trả None."""
import pytest

from src.qr import parse_qr_text


def test_vietnamese_json_keys():
    result = parse_qr_text('{"soTien": "125.000đ", "soNguoi": 2}')
    assert result["amount"] == 125_000
    assert result["party_size"] == 2


def test_english_json_keys():
    result = parse_qr_text('{"amount": 55000, "party_size": "3"}')
    assert result["amount"] == 55_000
    assert result["party_size"] == 3


def test_url_query():
    result = parse_qr_text("https://pay.example.vn/checkout?amount=125000&party_size=4")
    assert result["amount"] == 125_000
    assert result["party_size"] == 4


def test_merchant_name_from_json_and_url():
    """QR của cổng thanh toán ghi tên quán trong JSON/URL — dùng được cho ô tên quán."""
    assert parse_qr_text('{"amount": 42000, "merchant_name": "Quán Ốc"}')["merchant_name"] == "Quán Ốc"
    result = parse_qr_text("https://pay.example.vn/q?amount=42000&shop=Ph%E1%BB%9F")
    assert result["merchant_name"] == "Phở"


def test_bare_digits():
    result = parse_qr_text("125000")
    assert result["amount"] == 125_000
    assert result["party_size"] is None


def test_short_k_notation():
    assert parse_qr_text("125k")["amount"] == 125_000


@pytest.mark.parametrize("garbage", ["", "xin chao", "https://example.com/khong-co-gi", "{}"])
def test_garbage_never_raises_and_returns_none(garbage):
    result = parse_qr_text(garbage)
    assert result == {
        "amount": None,
        "party_size": None,
        "merchant_name": None,
        "bank_bin": None,
        "account": None,
        "raw": garbage,
    }


def test_absurd_party_size_is_dropped():
    """Số người vô lý thì bỏ, không đoán — người dùng tự nhập."""
    result = parse_qr_text('{"amount": 90000, "party_size": 99}')
    assert result["amount"] == 90_000
    assert result["party_size"] is None


def test_out_of_range_amount_is_rejected():
    """1.000đ là mức tối thiểu; dưới ngưỡng nghĩa là QR không phải hoá đơn."""
    assert parse_qr_text("500")["amount"] is None


# VietQR/EMVCo: chuỗi TLV "2 số tag + 2 số độ dài + giá trị", tag 54 = số tiền,
# 59 = tên đơn vị nhận tiền. Đây là dạng QR in trên bill chuyển khoản thật.
VIETQR_DYNAMIC = (
    "00020101021238570010A0000007270127000697042201130123456789012020"
    "8QRIBFTTA530370454061250005802VN5912NGUYEN VAN A6005HANOI62120108BILL12346304DF92"
)
VIETQR_STATIC = (
    "00020101021238570010A0000007270127000697042201130123456789012020"
    "8QRIBFTTA53037045802VN5912NGUYEN VAN A6005HANOI62120108BILL12346304FBA9"
)


def test_vietqr_dynamic_amount_and_merchant():
    result = parse_qr_text(VIETQR_DYNAMIC)
    assert result["amount"] == 125_000
    assert result["merchant_name"] == "NGUYEN VAN A"
    assert result["party_size"] is None


def test_vietqr_static_has_no_amount_but_keeps_merchant():
    """QR tĩnh (không gắn số tiền) vẫn đọc được tên quán, số tiền để trống."""
    result = parse_qr_text(VIETQR_STATIC)
    assert result["amount"] is None
    assert result["merchant_name"] == "NGUYEN VAN A"


def test_vietqr_amount_with_decimals():
    """Một số ngân hàng ghi ``125000.00`` — không được biến thành 12.500.000."""
    payload = VIETQR_DYNAMIC.replace("5406125000", "5409125000.00")
    assert parse_qr_text(payload)["amount"] == 125_000


def test_vietqr_dynamic_exposes_recipient_account():
    """Tag 38 ghi BIN + số tài khoản người nhận — cần để mở app ngân hàng."""
    result = parse_qr_text(VIETQR_DYNAMIC)
    assert result["bank_bin"] == "970422"
    assert result["account"] == "0123456789012"


def test_vietqr_static_exposes_recipient_account():
    result = parse_qr_text(VIETQR_STATIC)
    assert result["bank_bin"] == "970422"
    assert result["account"] == "0123456789012"


def test_non_napas_merchant_account_is_not_guessed():
    """Tag 38 của ví khác NAPAS đánh số khác — đoán ra là chuyển nhầm người."""
    payload = VIETQR_DYNAMIC.replace("A000000727", "A000000999")
    result = parse_qr_text(payload)
    assert result["bank_bin"] is None
    assert result["account"] is None


def test_json_qr_has_no_recipient_account():
    """QR của cổng thanh toán không có số tài khoản — không được bịa ra."""
    result = parse_qr_text('{"amount": 42000, "merchant_name": "Quán Ốc"}')
    assert result["account"] is None
    assert result["bank_bin"] is None


def test_emv_without_vietqr_marker_is_not_guessed():
    """Mọi chuỗi TLV đều có tag 00; chỉ nhận khi có tag 53 (tiền tệ) đúng chuẩn."""
    assert parse_qr_text("000201")["amount"] is None
