"""QR mã hóa đơn điện tử VN: decode → đối chiếu total/vendor → confidence."""
import base64
import io

import pytest

from src.domain.models import Invoice
from src.extract import extractor as ext


def _qr_png(payload: str) -> bytes:
    """Sinh ảnh QR thật từ payload (cv2 encoder) để test round-trip decode."""
    import cv2
    enc = cv2.QRCodeEncoder_create()
    ok, buf = cv2.imencode(".png", enc.encode(payload))
    assert ok
    return buf.tobytes()


# ---------- Parse payload ----------

def test_parse_qr_json_keys():
    """JSON payload (key VN) → total/vendor/số hiệu/MST, trusted=True."""
    p = ext._parse_qr_payload(
        '{"tong_tien":"1.500.000","ten_don_vi":"CONG TY ABC",'
        '"so_hd":"HD-001","mst":"0101234567"}'
    )
    assert p["total"] == 1500000
    assert p["vendor"] == "CONG TY ABC"
    assert p["invoice_number"] == "HD-001"
    assert p["tax_id"] == "0101234567"
    assert p["trusted"] is True


def test_parse_qr_url_params():
    """URL ?total=..&seller=.. → query params, trusted."""
    p = ext._parse_qr_payload(
        "https://einvoice.gov.vn/i?total=1%2C500%2C000&seller=CONG+TY+DEF"
    )
    assert p["total"] == 1500000
    assert p["vendor"] == "CONG TY DEF"
    assert p["trusted"] is True


def test_parse_qr_url_cct_pipe_heuristic():
    """URL có cct=<b64 chuỗi | (CCT)> → total heuristic, KHÔNG trusted."""
    pipe = "01GTGT|HD-001|01/001|K24TCD|0101234567|CONG TY ABC|20260501|1500000"
    cct = base64.urlsafe_b64encode(pipe.encode()).decode()
    p = ext._parse_qr_payload(f"https://x.vn/i?key=K&cct={cct}")
    assert p["total"] == 1500000
    assert p["trusted"] is False


def test_parse_qr_b64_json_and_garbage():
    """base64(JSON) → parse được; text rác → không phỏng đoán total."""
    raw = base64.b64encode('{"total":"900000","seller":"XYZ"}'.encode()).decode()
    p = ext._parse_qr_payload(raw)
    assert p["total"] == 900000 and p["trusted"] is True

    p = ext._parse_qr_payload("xin chao the gioi 123")
    assert p["total"] is None and p["trusted"] is False


# ---------- Đối chiếu ----------

def test_crosscheck_match_boosts_confidence():
    """Total QR khớp OCR → +0.25 confidence; vendor khớp (fuzzy) → +0.1."""
    inv = Invoice(total=1500000, vendor="CONG TY ABC CO LTD", confidence=0.6)
    ext._apply_qr_crosscheck(inv, '{"tong_tien":"1500000","ten_don_vi":"CONG TY ABC"}')
    assert inv.total == 1500000
    assert inv.confidence == pytest.approx(0.95)  # 0.6 + 0.25 + 0.1
    # QR xác nhận → field được đánh dấu verified (ô xanh trên UI review)
    assert inv.provenance["total"].source == "qr"
    assert inv.provenance["total"].confidence == 1.0


def test_crosscheck_mismatch_trusted_qr_wins():
    """OCR đọc sai total (thiếu số 0) → QR (key JSON) sửa lại, ghi provenance."""
    inv = Invoice(total=1500000, vendor="unknown", confidence=0.6)
    ext._apply_qr_crosscheck(inv, '{"tong_tien":"15000000"}')
    assert inv.total == 15000000
    assert inv.provenance["total"].source == "qr"
    assert inv.confidence == pytest.approx(0.6)  # mâu thuẫn → không boost


def test_crosscheck_pipe_mismatch_ignored():
    """Chuỗi | heuristic không khớp → bỏ qua (không biết bên nào sai), không boost."""
    inv = Invoice(total=1500000, vendor="unknown", confidence=0.6)
    pipe = "01GTGT|HD-1|010123456789|20260501|9999999"
    ext._apply_qr_crosscheck(inv, pipe)
    assert inv.total == 1500000
    assert inv.confidence == pytest.approx(0.6)


def test_crosscheck_fills_missing_fields():
    """OCR thiếu total/vendor/số hiệu → QR điền (fill), confidence theo nhịp."""
    inv = Invoice(total=0.0, vendor="unknown", confidence=0.2)
    ext._apply_qr_crosscheck(
        inv,
        '{"tong_tien":"750000","ten_don_vi":"CONG TY GHI","so_hd":"HD-777","mst":"0109999999"}',
    )
    assert inv.total == 750000
    assert inv.vendor == "CONG TY GHI"
    assert inv.invoice_number == "HD-777"
    assert inv.supplier_tax_id == "0109999999"
    assert inv.provenance["total"].source == "qr"
    assert inv.confidence == pytest.approx(0.55)  # 0.2 + 0.25 + 0.1


# ---------- End-to-end ----------

def test_extract_invoice_image_with_qr():
    """Ảnh chỉ chứa QR (OCR không đọc được chữ) → total/vendor từ QR."""
    png = _qr_png('{"tong_tien":"2500000","ten_don_vi":"CONG TY QR","so_hd":"QR-001"}')
    inv = ext.extract_invoice(png, "image/png", source_file="qr.png", user_id="u1")
    assert inv.total == 2500000
    assert inv.vendor == "CONG TY QR"
    assert inv.invoice_number == "QR-001"
    assert inv.provenance["total"].source == "qr"
    assert inv.confidence > 0


def test_extract_invoice_pdf_with_qr():
    """PDF trang 1 có QR (HĐ điện tử in PDF) → decode từ bản render."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader

    png = _qr_png('{"tong_tien":"3000000","ten_don_vi":"CONG TY PDF"}')
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawImage(ImageReader(io.BytesIO(png)), 100, 400, 180, 180, mask="auto")
    c.save()
    inv = ext.extract_invoice(buf.getvalue(), "application/pdf",
                              source_file="qr.pdf", user_id="u1")
    assert inv.total == 3000000
    assert inv.vendor == "CONG TY PDF"
    assert inv.provenance["total"].source == "qr"


def test_extract_text_unaffected_by_qr():
    """File text (không có QR) → provenance chỉ từ regex, không có nguồn qr."""
    text = "Số hóa đơn: HD-777\nNgười bán: Công ty ABC\nTổng cộng tiền thanh toán: 1,500,000"
    inv = ext.extract_invoice(text.encode(), "text/plain", source_file="b.txt")
    assert inv.total == 1500000
    assert all(p.source == "regex" for p in inv.provenance.values())
