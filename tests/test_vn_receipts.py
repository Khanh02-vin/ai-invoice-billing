"""Regression 2 hóa đơn bán lẻ VN thật (PosApp trà sữa + WinMart) — tầng regex.

Text fixture gõ tay đúng thứ tự in trên hóa đơn; biến thể không dấu mô phỏng
OCR mất dấu. Ảnh thật không đưa vào unit test (chỉ đo bằng script đối chiếu).
"""
import src.extract.extractor as ex
from src.extract.extractor import OcrLine, _strip_accents, extract_from_text

POSAPP_RECEIPT = """HN034 - Số 76 Tô Hiệu,
Phường Lê Chân, Thành
phố Hải Phòng
Số 76 Tô Hiệu, Phường Lê Chân,
Thành phố Hải Phòng
0347433293
Phiếu thanh toán
Số phiếu DV00004
Ngày tảo 2026-11-01 09:40
T.Ngän: Tại cửa hàng
HTNGHN034TN
Hồng Trà Đài Loan (Lớn 1000cc)
1 21,000
+ 60% đường x 1
+ Bình thường x 1
Tổng SL món 1
Thành tiền 21,000đ
Tổng thanh toán 21,000đ
+ Tiền mặt 21,000đ
Tiền thối 9,000đ
Phần mềm PosApp v26.10.0"""

WINMART_RECEIPT = """WinMart+
SIÊU THỊ TÍNH TIỀN
28/10/2026
08:30|MSCH:2BRT|PTTT:2BRT|01261003472
NV:09051529|Mã CQT: M1-26-7T6D3-10036903472
Mặt hàng KM T.Tiền
VINAMILK Sữa tươi nguyên kđ...
37,300
7DAYS Bánh sừng bò mứt t..
11,900
TỔNG TIỀN HÀNG 49,200
Tiền cần thanh toán 49,200
Truy cấp xuathoadon.winmart.vn trong 60 phút
để xuất hóa đơn (Mã xác thực HD: O3B9). Thông
tin sai, xin miễn chịu trách nhiệm."""


def _unaccent(s: str) -> str:
    """Mô phỏng OCR mất dấu (RapidOCR/tesseract thường bỏ dấu tiếng Việt)."""
    return _strip_accents(s).replace("đ", "d").replace("Đ", "D")


# --- PosApp: số phiếu, ngày ISO, tổng, tiền tệ ---


def test_posapp_invoice_number():
    """'Số phiếu DV00004' → DV00004."""
    assert extract_from_text(POSAPP_RECEIPT).invoice_number == "DV00004"


def test_posapp_invoice_number_unaccented():
    """OCR mất dấu: 'So phieu DV00004' vẫn nhận."""
    inv = extract_from_text(_unaccent(POSAPP_RECEIPT))
    assert inv.invoice_number == "DV00004"


def test_posapp_issue_date_iso():
    """'Ngày tảo 2026-11-01 09:40' → 2026-11-01 (chữ xen giữa label và số)."""
    assert extract_from_text(POSAPP_RECEIPT).issue_date == "2026-11-01"


def test_posapp_issue_date_unaccented():
    """'Ngay tao 2026-11-01 09:40' (mất dấu) vẫn nhận."""
    assert extract_from_text(_unaccent(POSAPP_RECEIPT)).issue_date == "2026-11-01"


def test_posapp_issue_date_glued_ocr():
    """OCR dán liền như ảnh thật: 'Ngaytao 2026-11-0109:40' (mất space trong
    nhãn, ngày-giờ dính nhau) — và 'Ngày tạo' đúng chính tả (ạ) cũng nhận."""
    base = "Phiếu thanh toán\nSố phiếu DV00004\n{T}\nTổng thanh toán 21,000đ"
    assert extract_from_text(base.format(T="Ngaytao 2026-11-0109:40")).issue_date == "2026-11-01"
    assert extract_from_text(base.format(T="Ngày tạo 2026-11-01 09:40")).issue_date == "2026-11-01"


def test_posapp_total():
    """'Tổng thanh toán 21,000đ' → 21000."""
    assert extract_from_text(POSAPP_RECEIPT).total == 21000.0


def test_posapp_currency_vnd():
    """Đuôi 'đ' sau số → VND, không phải USD mặc định."""
    assert extract_from_text(POSAPP_RECEIPT).currency == "VND"


def test_posapp_currency_unaccented():
    """OCR viết '21,000d' (d thường) vẫn → VND."""
    inv = extract_from_text(_unaccent(POSAPP_RECEIPT))
    assert inv.currency == "VND"
    assert inv.total == 21000.0


def test_posapp_vendor_not_timestamp():
    """Dòng đầu OCR là timestamp → vendor lấy dòng kế, không nhận nhầm."""
    text = "2026-11-01 09:40\nHN034 - Số 76 Tô Hiệu,\nPhường Lê Chân\nPhiếu thanh toán"
    assert extract_from_text(text).vendor == "HN034 - Số 76 Tô Hiệu,"


# --- WinMart: tổng tiền hàng + tiền cần thanh toán ---


def test_winmart_total():
    """'Tiền cần thanh toán 49,200' → 49200 (dòng dưới 'TỔNG TIỀN HÀNG')."""
    assert extract_from_text(WINMART_RECEIPT).total == 49200.0


def test_winmart_total_unaccented():
    """'Tong tien hang 49,200' / 'Tien can thanh toan 49,200' mất dấu vẫn nhận."""
    inv = extract_from_text(_unaccent(WINMART_RECEIPT))
    assert inv.total == 49200.0


def test_winmart_currency_vnd():
    """Nhãn tiền tiếng Việt, không ký hiệu ngoại tệ → VND."""
    assert extract_from_text(WINMART_RECEIPT).currency == "VND"


def test_winmart_issue_date():
    """'28/10/2026' → 2026-10-28 (đã hoạt động — canh regression)."""
    assert extract_from_text(WINMART_RECEIPT).issue_date == "2026-10-28"


def test_winmart_vendor_not_amount():
    """Dòng đầu OCR là số tiền → vendor lấy dòng kế, không nhận nhầm."""
    text = "37,300\nWinMart+\n28/10/2026\nTỔNG TIỀN HÀNG 49,200"
    assert extract_from_text(text).vendor == "WinMart+"


# --- OCR thật: box trả lộn xộn, nhãn/số tiền tách thành 2 box cùng in-hàng ---


_FAKE_OCR_LINES = [
    OcrLine("21,000d", (120, 200, 180, 214)),
    OcrLine("HN034 - So 76 To Hieu,", (10, 20, 170, 34)),
    OcrLine("Tong thanh toan", (10, 202, 110, 216)),
    OcrLine("So phieu DV00004", (10, 60, 140, 74)),
]
_OCR_TEXT_SORTED = (
    "HN034 - So 76 To Hieu,\nSo phieu DV00004\nTong thanh toan 21,000d"
)


def test_ocr_text_sorted_by_bbox(monkeypatch):
    """Box OCR bị xáo trộn thứ tự list → text phải theo chiều đọc (y rồi x),
    box cùng in-hàng gộp 1 dòng: 'Tong thanh toan 21,000d'."""
    monkeypatch.setattr(ex, "_paddle_ocr_lines", lambda content: _FAKE_OCR_LINES)
    assert ex._extract_ocr_text(b"img") == _OCR_TEXT_SORTED


def test_image_pipeline_text_sorted(monkeypatch):
    """Pipeline ảnh thật (_extract_text_with_layout) cũng phải sort, không join
    theo thứ tự box OCR trả về."""
    monkeypatch.setattr(ex, "_paddle_ocr_lines", lambda content: _FAKE_OCR_LINES)
    text, layout = ex._extract_text_with_layout(b"img", "image/png")
    assert text == _OCR_TEXT_SORTED
    assert layout is not None and len(layout) == 4


# --- Ảnh chụp bị xoay 90°: OCR lại 2 hướng xoay, giữ hướng conf cao ---


def test_ocr_with_rotation_picks_upright():
    """Đa số box dọc (ảnh xoay) → OCR lại sau khi xoay, không trả text hướng gốc."""
    from PIL import Image

    def ocr_fn(arr):
        h, w = arr.shape[:2]
        if h > w:
            return [OcrLine("Hoa don 350.000", (10, 10, 50, 20), 0.9)]
        return [OcrLine("0.000 35 nod aoH", (10, 10, 20, 50), 0.5)]

    lines = ex._ocr_with_rotation(ocr_fn, Image.new("RGB", (40, 20)))
    assert lines[0].text == "Hoa don 350.000"


def test_ocr_with_rotation_keeps_upright():
    """Ảnh đúng hướng (box ngang) → OCR 1 lần, không xoay."""
    from PIL import Image

    def ocr_fn(arr):
        return [OcrLine("Hoa don 350.000", (10, 5, 50, 15), 0.9)]

    assert ex._ocr_with_rotation(ocr_fn, Image.new("RGB", (60, 20)))[0].text == "Hoa don 350.000"


def test_ocr_rotation_prefers_label_before_amount():
    """Hướng mirror conf cao hơn vẫn thua hướng có nhãn tiền kề số — hóa đơn
    in số tiền bên phải nhãn nên 'Tong thanh toan 21,000d' mới là chiều đọc đúng."""
    from PIL import Image

    def ocr_fn(arr):
        if arr[0, 19, 0] > 0:  # pixel đỏ ở góc trên-phải → ảnh đã xoay 90° CW (đúng)
            return [OcrLine("Tong thanh toan 21,000d", (10, 10, 90, 20), 0.6)]
        if arr.shape[0] == 40 and arr[39, 0, 0] > 0:  # góc dưới-trái → mirror
            return [OcrLine("21,000d Tong thanh toan", (10, 10, 90, 20), 0.9)]
        return [OcrLine("21,000d Tong thanh toan", (10, 10, 20, 50), 0.8)]

    img = Image.new("RGB", (40, 20))
    img.putpixel((0, 0), (1, 0, 0))
    assert ex._ocr_with_rotation(ocr_fn, img)[0].text == "Tong thanh toan 21,000d"


def test_ocr_rotation_rejects_cross_line_match():
    """Mirror mà nhãn nằm cuối dòng trên + số đầu dòng dưới vẫn phải thua —
    regex 'nhãn...số' ăn cả xuống dòng nên match xuyên dòng là ảo, không phải
    'nhãn kề số' trên cùng một dòng in."""
    from PIL import Image

    def ocr_fn(arr):
        if arr[0, 19, 0] > 0:  # xoay đúng: nhãn + số cùng một dòng
            return [OcrLine("Tien can thanh toan 49,200", (10, 10, 190, 20), 0.6)]
        if arr.shape[0] == 40 and arr[39, 0, 0] > 0:  # mirror: số trước nhãn, 2 dòng
            return [
                OcrLine("49,200", (10, 10, 40, 20), 0.9),
                OcrLine("Tien can thanh toan", (50, 10, 190, 20), 0.9),
                OcrLine("49,200", (10, 30, 40, 40), 0.9),
                OcrLine("TONG TIEN HANG", (50, 30, 190, 40), 0.9),
            ]
        return [OcrLine("49,200 Tien can thanh toan", (10, 10, 20, 50), 0.8)]

    img = Image.new("RGB", (40, 20))
    img.putpixel((0, 0), (1, 0, 0))
    assert ex._ocr_with_rotation(ocr_fn, img)[0].text == "Tien can thanh toan 49,200"
