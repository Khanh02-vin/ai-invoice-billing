"""Trích xuất trường hóa đơn từ file.

Hỗ trợ: tiếng Anh + Hóa đơn GTGT điện tử tiếng Việt + ảnh scan (OCR).
Regex chính; LLM fallback khi regex đọc thiếu (confidence < 0.8).
ponytail: chuỗi OCR Paddle→Tesseract, LLM qua provider có thể inject.

Nguyên tắc (PLAN mục 3):
- Mỗi field lưu value, confidence, source, page, bbox/text_span nếu có.
- Provider adapters cho regex, OCR và LLM; timeout/retry/cost cap.
"""
import difflib
import json
import os
import re
import unicodedata
from typing import NamedTuple, Optional, Tuple
from ..domain.models import Invoice, FieldProvenance, JobStatus, LineItem
from ..llm.base import get_llm_provider, LLMProvider


class OcrLine(NamedTuple):
    """1 dòng OCR: text + bbox (x0, y0, x1, y1) — dùng cho parse theo vị trí."""

    text: str
    box: Tuple[float, float, float, float]

def _llm_threshold() -> float:
    """Ngưỡng gọi LLM (confidence < threshold).

    Calibrate bởi scripts/calibrate_llm_threshold.py trên 987 receipt SROIE có GT:
    knee của đường (chi phí gọi LLM → recall lỗi) là 0.7 — 13.3% receipt được gọi,
    bắt 24.9% ca có lỗi với precision 91.6%. Bước kế (0.9) đòi 6× chi phí (79.2%)
    cho +62.6 điểm recall nhưng precision rơi còn 53.8% → dừng ở knee.
    Override khi cần: env LLM_CALL_THRESHOLD.
    """
    try:
        return float(os.getenv("LLM_CALL_THRESHOLD", "0.7"))
    except ValueError:
        return 0.7


# --- Nhãn song ngữ (Anh + Việt) ---
_INVOICE_NO_RE = re.compile(
    r"(?:invoice\s*(?:no|number|#)|số\s*h[oó][aá]\s*đơn|hđ\s*số)\s*[:#]?\s*([A-Z0-9\-_/]+)", re.I)
# ponytail: anchored đầu dòng tránh "from the date of purchase..." bắt nhầm trong receipt thật
def _clean_vendor_line(s: str) -> str:
    """Strip trailing date/time from first-line vendor (e.g. 'UNIHAKKA ... 02 APR 2018 18:31')."""
    s = re.sub(r"\s+\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}.*$", "", s).strip()
    s = re.sub(r"\s+\d{1,2}[-/]\d{1,2}[-/]\d{2,4}.*$", "", s).strip()
    return s

_VENDOR_RE = re.compile(
    r"(?m)^\s*(?:from|vendor|seller|supplier|người\s*bán(?!\s*hàng))\s*[:#]?\s*(.+)", re.I)
_DATE_RE = re.compile(
    r"(?:invoice\s*date|issue\s*date|dated|date(?:\s*time)?|ngày)\s*[:#]?\s*"
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.I)
_DUE_RE = re.compile(
    r"(?:due\s*date|payment\s*due)\s*[:#]?\s*"
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.I)
# "cộng tiền hàng hóa" (subtotal) bị loại; ưu tiên "tổng cộng tiền thanh toán".
# Cho receipt thật: hỗ trợ "total payable", "nett total", "final total", "amount due/balance due",
# và tiền tệ chèn giữa label và số (TOTAL RM/USD), chọn số cuối cùng (dòng total ở dưới).
# ponytail: capture bắt buộc bắt đầu bằng chữ số để tránh bắt "." rời rạc như capture group.
_TOTAL_LABELS = (
    r"nett?\s*total|total\s*due|total\s*payable|final\s*total|total\s*amount|grand\s*total|"
    r"balance\s*due|amount\s*due|rounding|tổng\s*cộng\s*tiền\s*thanh\s*toán|"
    r"total\s*sales|total\s*includes|net\s*amt|net\s*amount|\btotal\b|sub[\s-]*total"
)


def _amount_re(labels: str) -> "re.Pattern":
    """Regex 'nhãn total + số' — chia sẻ cho keyword path và layout path (label set khác nhau)."""
    return re.compile(
        rf"(?P<label>{labels})\s*(?:\(\s*)?"
        rf"(?:incl(?:usive)?[^0-9:#\n]*?(?:\d+(?:[.,]\d+)?%[^0-9:#\n]*?)?"
        rf"|after\s*rounding[^0-9:#\n]*?"
        rf"|[^0-9:#\n]{{0,20}}?)?"
        rf"\s*[:#]?\s*"
        rf"(?:rm|usd|eur|vnd|gbp|jpy|myr|\$)?\s*"
        rf"(?P<num>[0-9]+(?:[.,][0-9]+)*)", re.I)


_AMOUNT_RE = _amount_re(_TOTAL_LABELS)

def _pick_total(text: str, layout: Optional[list] = None) -> Optional[str]:
    """Chọn số total đúng trên receipt thật: loại 'tax total'/subtotal/total qty/count,
    ƯU TIÊN layout (bbox) khi có — số gần label, dòng dưới thắng; không có layout thì
    keyword: ưu tiên nhãn cụ thể (grand/nett/final/payable/due), hòa nhất bằng dòng total ở dưới cùng.
    Bonus cho dòng mà số là token cuối (loại số giao dịch kiểu 'TOTAL 1010 008 00B0498')."""
    if layout:
        v = _pick_total_layout(layout)
        if v:
            return v
    cands = []
    for m in _AMOUNT_RE.finditer(text):
        ctx = m.group(0).lower()
        label_low = m.group("label").lower()
        num = m.group("num")
        is_after_rounding = "after rounding" in ctx or "after rounding" in text[max(0,m.start()-30):m.end()].lower()
        is_incl_gst = "incl" in ctx
        if not (is_after_rounding or is_incl_gst):
            if any(k in ctx for k in ("sub", "qty", "quantity", "count", "item", "exclud",
                                      "excl", "tax", "included", "total gst", "adjustment",
                                      "point")):  # TOTAL POINTS (điểm tích lũy) ≠ tiền
                continue
        if "adjustment" in ctx:
            continue
        if "rounding" in label_low:
            try:
                val = float(num.replace(",", ""))
            except:
                val = 0
            if val < 1.0:
                continue
        prev = text[max(0, m.start() - 40):m.start()]
        prev_low = prev.lower()
        if not (is_incl_gst or is_after_rounding) and ("item count" in prev_low or "item count" in (prev_low + " " + ctx)):
            continue
        if not (is_incl_gst or is_after_rounding) and re.search(r"\b(?:item|count|qty|quantity|no|number|pcs|unit|pos|ref|trans)"
                     r"\s+total\s*$|included\s+in\s*$", prev, re.I):
            continue
        # Chỉ chặn khi qty/item nằm CÙNG DÒNG với nhãn ("... QTY 2 | TOTAL") — guard cũ soi
        # cả 40 ký tự trước nên loại oan dòng "TOTAL : 31.00" ngay dưới header bảng
        # "DESCRIPTION QTY PRICE AMOUNT" (receipt thật SROIE, +12 receipt total).
        if (not (is_incl_gst or is_after_rounding) and label_low.strip() == "total"
                and any(k in prev_low.rsplit("\n", 1)[-1] for k in ("item", "count", "qty", "quantity"))):
            continue
        lab = label_low
        rank = 3
        if any(k in lab for k in ("payable", "nett", "grand", "final", "due", "amount")):
            rank = 4
        if "rounding" in lab:
            rank = 6
        if "after rounding" in ctx or "after rounding" in text[max(0,m.start()-30):m.end()].lower():
            rank = 6
        if "incl" in ctx:
            rank = 5
        if "tổng cộng tiền thanh toán" in lab:
            rank = 5
        rest = text[m.end():].split("\n", 1)[0].strip()
        if not rest or not re.search(r"\d", rest):
            rank += 1
        cands.append((rank, m))
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], x[1].start()))
    return cands[0][1].group("num")


# --- Chọn total theo vị trí (layout-aware, dùng bbox từ OCR) ---
# Khác keyword path: thêm nhãn bán lẻ VN quen thuộc ("tổng cộng", "tổng tiền", "cần trả"...)
# vì keyword chỉ có "tổng cộng tiền thanh toán"; match trên text KHÔNG dấu (OCR hay mất dấu);
# loại "tổng cộng tiền hàng" (subtotal). Chỉ áp khi có layout — keyword path giữ nguyên.
_LAYOUT_LABELS = (
    r"nett?\s*total|total\s*due|total\s*payable|final\s*total|total\s*amount|grand\s*total|"
    r"balance\s*due|amount\s*due|total\s*includes|net\s*amt|net\s*amount|\btotal\b|sub[\s-]*total|"
    r"tong\s*cong\b(?!\s*tien\s*hang)|tong\s*cong\s*tien\s*thanh\s*toan|tong\s*phai\s*tra\b|"
    r"tong\s*tien\b|tong\s*thanh\s*toan\b|\bcan\s*tra\b|khach\s*phai\s*tra\b"
)
_LAYOUT_AMOUNT_RE = _amount_re(_LAYOUT_LABELS)
_LAYOUT_LABEL_RE = re.compile(rf"(?P<label>{_LAYOUT_LABELS})", re.I)


def _strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt (giữ số/khoảng trắng) — OCR hay mất dấu."""
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _layout_bad(label: str, ctx: str, num: str) -> bool:
    """Loại sub-total/qty/tax/adjustment... — cùng triết lý keyword path."""
    if "after rounding" in ctx or "incl" in ctx:
        return False
    if any(k in ctx for k in ("sub", "qty", "quantity", "count", "item", "exclud",
                              "excl", "tax", "included", "total gst", "adjustment")):
        return True
    if "rounding" in label.lower():
        try:
            return float(num.replace(",", "")) < 1.0
        except ValueError:
            return True
    return False


def _layout_priority(label: str, ctx: str) -> int:
    """Xếp hạng nhãn (cao thắng) — bám rank của keyword path."""
    lab = label.lower()
    if "tong cong tien thanh toan" in lab:
        return 5
    if any(k in lab for k in ("payable", "nett", "grand", "final", "due", "amount")):
        return 4
    if any(k in lab for k in ("tong tien", "tong phai tra", "tong thanh toan",
                              "can tra", "khach phai tra")):
        return 4
    if "incl" in ctx:
        return 5
    if "after rounding" in ctx or "rounding" in lab:
        return 6
    return 3


def _pick_total_layout(layout: list) -> Optional[str]:
    """Chọn total theo bbox: chỉ nhãn total mạnh (loại sub/qty/tax); số lấy trên cùng
    dòng hoặc ≤2 dòng ngay dưới nhãn (số gần nhất); cùng nhãn → dòng dưới thắng.
    Sửa các fail keyword đã ghi nhận: số nằm DƯỚI dòng "Tổng cộng" (MCOCR), nhầm
    SUB-TOTAL vs TOTAL, chọn nhầm số không gần label."""
    best_key = None
    best_num = None
    for i, ln in enumerate(layout):
        line = _strip_accents(ln.text)
        m_lab = _LAYOUT_LABEL_RE.search(line)
        if not m_lab:
            continue
        matches = list(_LAYOUT_AMOUNT_RE.finditer(line))
        for m in matches:
            ctx = line[max(0, m.start() - 12):m.end()].lower()
            if _layout_bad(m.group("label"), ctx, m.group("num")):
                continue
            # (nhãn, vị trí dọc, offset): nhãn mạnh thắng → cùng nhãn dòng dưới thắng
            key = (_layout_priority(m.group("label"), ctx), ln.box[1], m.start())
            if best_key is None or key > best_key:
                best_key, best_num = key, m.group("num")
        if matches:
            continue
        # Nhãn không có số trên dòng → số ở ≤2 dòng ngay dưới (đúng fail MCOCR);
        # dừng ở dòng có chữ đầu tiên không có số, không trôi xa khỏi label.
        num = None
        for j in (i + 1, i + 2):
            if j >= len(layout):
                break
            t = layout[j].text.strip()
            if not t:
                continue
            nm = _NUM_TOKEN_RE.search(t)
            if nm:
                num = nm.group(0)
                break
            break
        if num is None:
            continue
        ctx = line[max(0, m_lab.start() - 12):m_lab.end()].lower()
        if _layout_bad(m_lab.group("label"), ctx, num):
            continue
        key = (_layout_priority(m_lab.group("label"), ctx), ln.box[1], 0)
        if best_key is None or key > best_key:
            best_key, best_num = key, num
    return best_num


def _parse_number(s: str) -> float:
    """Parse số theo quy ước dot-thousands (VN/EU: 30.900.000, 1.234,56) —
    SMS ngân hàng/MoMo dùng chung parser với invoice (xem _to_float)."""
    return _to_float(s, vi=True)


def _extract_text_with_layout(content: bytes, mime_type: str) -> Tuple[str, Optional[list]]:
    """Trả về (text, layout) — layout (list[OcrLine]) chỉ có khi text sinh từ OCR."""
    if mime_type == "application/pdf":
        text = _pdf_text_layer(content)
        if text.strip():
            return text, None
        layout = _pdf_ocr_lines(content)
        return "\n".join(l.text for l in layout), (layout or None)
    if mime_type.startswith("image/"):
        layout = _paddle_ocr_lines(content)
        if layout:
            return "\n".join(l.text for l in layout), layout
        return _tesseract_text(content), None
    if mime_type == "text/plain":
        return content.decode("utf-8", errors="ignore"), None
    return "", None


def _extract_text(content: bytes, mime_type: str) -> str:
    """Trích xuất text từ file theo MIME type (không kèm layout)."""
    return _extract_text_with_layout(content, mime_type)[0]


def _pdf_text_layer(content: bytes) -> str:
    """Text layer của PDF (pdfplumber → PyPDF2). PDF scan không có → trả rỗng."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _extract_pdf_text(content: bytes) -> str:
    """Trích xuất text từ PDF: text layer; rỗng (PDF quét) → render trang + OCR."""
    text = _pdf_text_layer(content)
    if text.strip():
        return text
    return "\n".join(l.text for l in _pdf_ocr_lines(content))


def _pdf_ocr_lines(content: bytes) -> list:
    """PDF quét (scan) không có text layer → render từng trang (pypdfium2) → OCR giữ bbox."""
    try:
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as doc:
            out = []
            for i in range(len(doc)):
                pil = doc[i].render(scale=2).to_pil()
                buf = io.BytesIO()
                pil.save(buf, format="PNG")
                out.extend(_paddle_ocr_lines(buf.getvalue()))
            return out
    except Exception:
        return []


def _paddle_ocr_lines(content: bytes) -> list:
    """OCR 1 ảnh bằng PaddleOCR — trả list[OcrLine] giữ bbox từng dòng ([] nếu lỗi)."""
    try:
        from paddleocr import PaddleOCR
        from PIL import Image
        import numpy as np
        # lang="vi": khớp pipeline benchmark (MCOCR/CORD) và hóa đơn tiếng Việt —
        # model "en" mất dấu tiếng Việt, đọc sai vendor/total.
        ocr = PaddleOCR(use_angle_cls=True, lang="vi")
        img = Image.open(io.BytesIO(content))
        result = ocr.ocr(np.array(img), cls=True)
        lines = []
        for line in (result[0] or []):
            box = line[0]
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(OcrLine(line[1][0], (min(xs), min(ys), max(xs), max(ys))))
        return lines
    except Exception:
        return []


def _extract_ocr_text(content: bytes) -> str:
    """OCR ảnh → text."""
    lines = _paddle_ocr_lines(content)
    if lines:
        return "\n".join(l.text for l in lines)
    return _tesseract_text(content)


def _tesseract_text(content: bytes) -> str:
    """Fallback OCR qua pytesseract (không có bbox)."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(content))
        return pytesseract.image_to_string(img)
    except Exception:
        return ""


def extract_from_text(text: str, source_file: str = "unknown") -> Invoice:
    """Trích xuất hóa đơn từ text thô — regex path, không LLM."""
    import uuid
    invoice = Invoice(id=str(uuid.uuid4()), source_file=source_file)

    # Invoice number
    m = _INVOICE_NO_RE.search(text)
    if m:
        invoice.invoice_number = m.group(1).strip()
        invoice.provenance["invoice_number"] = FieldProvenance(
            value=invoice.invoice_number, confidence=0.9, source="regex",
            text_span=m.group(0),
        )

    # Vendor
    m = _VENDOR_RE.search(text)
    if m:
        cand = m.group(1).strip()
        if len(cand) < 60 and "date of purchase" not in cand.lower() and "request tax" not in cand.lower():
            invoice.vendor = _clean_vendor_line(cand)
            invoice.provenance["vendor"] = FieldProvenance(
                value=invoice.vendor, confidence=0.85, source="regex",
                text_span=m.group(0),
            )
        else:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if lines:
                invoice.vendor = _clean_vendor_line(lines[0][:80])
                invoice.provenance["vendor"] = FieldProvenance(
                    value=invoice.vendor, confidence=0.85, source="regex",
                    text_span=lines[0],
                )
    else:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            first = lines[0]
            if len(first) >= 3 and not re.match(r"^(receipt|invoice|tax invoice|cash|date|doc no)", first, re.I):
                invoice.vendor = _clean_vendor_line(first[:80])
                invoice.provenance["vendor"] = FieldProvenance(
                    value=invoice.vendor, confidence=0.85, source="regex",
                    text_span=first,
                )
    # Issue date
    m = _DATE_RE.search(text)
    if m:
        invoice.issue_date = m.group(1)
        invoice.provenance["issue_date"] = FieldProvenance(
            value=invoice.issue_date, confidence=0.9, source="regex",
            text_span=m.group(0),
        )
    else:
        dm = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", text)
        if dm:
            try:
                import datetime as _dt2
                _months2 = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                _mo2 = _months2.get(dm.group(2).lower()[:3], 0)
                if _mo2:
                    invoice.issue_date = f"{dm.group(3)}-{_mo2:02d}-{int(dm.group(1)):02d}"
                    invoice.provenance["issue_date"] = FieldProvenance(value=invoice.issue_date, confidence=0.85, source="regex", text_span=dm.group(0))
            except: pass
        if not invoice.issue_date:
            gm = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", text)
            if gm:
                invoice.issue_date = _normalize_date(gm.group(1))
                invoice.provenance["issue_date"] = FieldProvenance(value=invoice.issue_date, confidence=0.85, source="regex", text_span=gm.group(0))

    # Due date
    m = _DUE_RE.search(text)
    if m:
        invoice.due_date = m.group(1)
        invoice.provenance["due_date"] = FieldProvenance(
            value=invoice.due_date, confidence=0.9, source="regex",
            text_span=m.group(0),
        )

    # Total
    total_str = _pick_total(text)
    if total_str:
        invoice.total = _parse_number(total_str)
        invoice.provenance["total"] = FieldProvenance(
            value=invoice.total, confidence=0.85, source="regex",
            text_span=total_str,
        )

    # Tax — tổng tất cả dòng thuế (GTGT + tiêu thụ đặc biệt + ...), loại "thuế suất" (rate)
    tax_re = re.compile(
        r"(?:thuế\s*gtgt|thuế(?!\s*suất)|\btax\b|\bvat\b|\bgst\b)(?!\s*(?:id|reg|no|#|code|summary|rate))"
        r"(?:(?!\s*[:#]?\s*[0-9.,]*\s*%).)*?"
        r"\s*[:#]?\s*(?:rm|usd|eur|vnd|gbp|jpy|myr|\$)?\s*([0-9]+(?:[.,][0-9]+)*)",
        re.I,
    )
    # Backup: tìm dòng chứa "thuế" và số
    tax_matches = list(tax_re.finditer(text))
    if tax_matches:
        total_tax = sum(_parse_number(m.group(1)) for m in tax_matches)
        invoice.tax = total_tax
        invoice.provenance["tax"] = FieldProvenance(
            value=invoice.tax, confidence=0.8, source="regex",
            text_span=" | ".join(m.group(0) for m in tax_matches),
        )

    # Currency detection
    if re.search(r"\b(vnd|₫|đồng)\b", text, re.I):
        invoice.currency = "VND"
    elif re.search(r"\b(usd|\$|dollar)\b", text, re.I):
        invoice.currency = "USD"
    elif re.search(r"\b(eur|€|euro)\b", text, re.I):
        invoice.currency = "EUR"
    elif re.search(r"\b(myr|rm|ringgit)\b", text, re.I):
        invoice.currency = "MYR"

    # Confidence tổng: dựa trên số field đã trích xuất
    fields_extracted = len(invoice.provenance)
    invoice.confidence = min(1.0, fields_extracted / 5.0)

    # Job status: nếu confidence thấp → REVIEW, ngược lại → REVIEW (luôn cần review)
    invoice.job_status = JobStatus.REVIEW
    invoice.extraction_provider = "regex"

    # Raw snippet
    invoice.raw_snippet = text[:500]

    return invoice


def extract_invoice(content: bytes, mime_type: str, source_file: str = "unknown", user_id: str = "") -> Invoice:
    """Trích xuất hóa đơn từ file content — hàm chính cho API upload."""
    import uuid
    text, layout = _extract_text_with_layout(content, mime_type)
    invoice = extract_from_text(text, source_file=source_file, layout=layout)
    # QR HĐ điện tử (ảnh/PDF): đối chiếu total/vendor — khớp → tăng confidence,
    # OCR đọc sai total → QR sửa. File text không có QR → None, không đổi gì.
    payload = _qr_decode(content, mime_type)
    if payload:
        _apply_qr_crosscheck(invoice, payload)
    invoice.user_id = user_id
    # id ngẫu nhiên, KHÔNG dùng _make_id: md5(số-hóa-đơn:filename) là PK toàn cục —
    # 2 user cùng filename/số sẽ đè lên hóa đơn của nhau. Chống trùng do repository lo.
    invoice.id = str(uuid.uuid4())
    return invoice


# --- QR mã hóa đơn điện tử VN: decode → đối chiếu total/vendor/MST ---
def _qr_decode(content: bytes, mime_type: str) -> Optional[str]:
    """Decode QR trong ảnh/PDF (trang 1) → payload text; không có/lỗi → None.

    cv2 detector trượt với ảnh nhỏ → thử lại sau khi phóng to ×4 + viền trắng."""
    if not (mime_type.startswith("image/") or mime_type == "application/pdf"):
        return None
    try:
        import cv2
        import numpy as np
        if mime_type.startswith("image/"):
            img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        else:
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(content) as doc:
                if not len(doc):
                    return None
                pil = doc[0].render(scale=2).to_pil()
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        if img is None:
            return None
        det = cv2.QRCodeDetector()
        data, _, _ = det.detectAndDecode(img)
        if not data:
            h, w = img.shape[:2]
            if max(h, w) <= 1500:
                big = cv2.resize(img, (w * 4, h * 4), interpolation=cv2.INTER_NEAREST)
                big = cv2.copyMakeBorder(big, 40, 40, 40, 40,
                                         cv2.BORDER_CONSTANT, value=(255, 255, 255))
                data, _, _ = det.detectAndDecode(big)
        return data or None
    except Exception:
        return None


# Khóa payload QR (đã chuẩn hóa: bỏ ký tự không alnum, lower) → field hóa đơn
_QR_FIELDS = {
    "total": ("total", "amount", "grandtotal", "totalamount", "tongtien",
              "tongtientt", "tongtienthanhtoan", "tongphaitra", "sotien", "thanhtien"),
    "vendor": ("vendor", "seller", "supplier", "tendonvi", "donviban", "tennguoiban",
               "nguoiban", "tenshop", "sellername", "suppliername", "tendoanhnghiep"),
    "tax_id": ("mst", "taxcode", "masothue", "sellertax", "suppliertax",
               "mangnt", "sellermst", "masothueban"),
    "invoice_number": ("invoicenumber", "invoiceno", "invno", "sohd", "sohieu",
                       "sohoaodon", "hoadonso", "sohoadon"),
}


def _qr_lookup(d: dict, field: str) -> Optional[str]:
    """Lấy value theo key alias; không khớp khóa nào → None (không phỏng đoán)."""
    aliases = set(_QR_FIELDS[field])
    for k, v in d.items():
        if re.sub(r"[^a-z0-9]", "", str(k).lower()) in aliases and v not in (None, ""):
            return str(v).strip()
    return None


def _qr_total(v) -> Optional[float]:
    """Giá trị total từ chuỗi QR ('1.500.000₫', '1,500,000.00', số raw) → float."""
    s = re.sub(r"[^\d.,]", "", str(v))
    if not s:
        return None
    try:
        t = _to_float(s, vi=True)
    except ValueError:
        return None
    return t if 0 < t < 1e12 else None


def _qr_fill(out: dict, d: dict) -> None:
    """Điền field từ dict key→value. Chỉ total LẤY THEO KEY mới được cờ trusted."""
    v = _qr_lookup(d, "total")
    if v is not None:
        t = _qr_total(v)
        if t is not None:
            out["total"], out["trusted"] = t, True
    for f in ("vendor", "tax_id", "invoice_number"):
        v = _qr_lookup(d, f)
        if v:
            out[f] = v


def _qr_flat(d: dict) -> dict:
    """Gộp dict 1 cấp lồng ({"cData": {...}} → {...}) — JSON vendor hay bọc thêm."""
    out = dict(d)
    for v in d.values():
        if isinstance(v, dict):
            out.update(v)
    return out


def _qr_b64(s: str) -> Optional[str]:
    """Decode base64 (cả urlsafe, thiếu padding) → text; không phải base64/UTF-8 → None."""
    import base64
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_").decode("utf-8")
    except Exception:
        return None


def _qr_pipe_total(payload: str) -> Optional[float]:
    """Tổng tiền từ chuỗi CCT dạng 'a|b|c' (KHÔNG key — thứ tự field chưa verify):
    bỏ ngày (8 số 19xx/20xx), MST (8-9+ số rơi vào khoảng tiền nhưng có dấu hiệu
    mã/NST), mã số0 đầu; lấy số lớn nhất còn lại. Heuristic → chỉ để đối chiếu."""
    best = None
    for f in payload.split("|"):
        f = f.strip()
        if not re.fullmatch(r"\d[\d.,]+", f) or re.fullmatch(r"[12]\d{7}", f):
            continue
        digits = re.sub(r"[.,]", "", f)
        if not 4 <= len(digits) <= 9 or digits.startswith("0"):
            continue
        try:
            v = _to_float(f, vi=True)
        except ValueError:
            continue
        if best is None or v > best:
            best = v
    return best


def _qr_merge(out: dict, sub: dict) -> None:
    """Gộp payload con (URL→cct / b64): total con trusted thắng; field khác nếu thiếu."""
    if sub["total"] is not None and (sub["trusted"] or out["total"] is None):
        out["total"], out["trusted"] = sub["total"], sub["trusted"]
    for f in ("vendor", "tax_id", "invoice_number"):
        if out[f] is None:
            out[f] = sub[f]


def _parse_qr_payload(payload: str, depth: int = 0) -> dict:
    """Parse payload QR HĐ điện tử → dict(total, vendor, tax_id, invoice_number, trusted).

    Nhận diện: JSON (thẳng hoặc base64) / URL ?key=..&cct=.. (cct b64 → JSON hoặc
    chuỗi |) / chuỗi | thuần. trusted=True khi total lấy theo KEY — giá trị đó
    đủ tin để sửa OCR; chuỗi | không key chỉ heuristic → đối chiếu, không ghi đè."""
    out = {"total": None, "vendor": None, "tax_id": None,
           "invoice_number": None, "trusted": False}
    if not payload or depth > 3:
        return out
    s = payload.strip()
    # JSON trực tiếp
    if s.startswith("{"):
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                _qr_fill(out, _qr_flat(d))
                return out
        except ValueError:
            pass
    # URL → query params; param lồng (cct/data/payload/q) → base64 → parse tiếp
    if s.startswith(("http://", "https://")):
        import urllib.parse
        qs = {k: v[0] for k, v in
              urllib.parse.parse_qs(urllib.parse.urlsplit(s).query).items() if v}
        _qr_fill(out, qs)
        for k in ("cct", "data", "payload", "q"):
            if k in qs:
                # parse_qs đổi '+' thành space — base64 chuẩn dùng '+' nên khôi phục
                dec = _qr_b64(qs[k].replace(" ", "+"))
                if dec:
                    _qr_merge(out, _parse_qr_payload(dec, depth + 1))
        return out
    # base64 của JSON / URL / chuỗi |
    dec = _qr_b64(s)
    if dec and dec != s:
        _qr_merge(out, _parse_qr_payload(dec, depth + 1))
        if out["total"] is not None:
            return out
    # Chuỗi | (CCT không key) — heuristic, không trusted
    if out["total"] is None and "|" in s:
        out["total"] = _qr_pipe_total(s)
    return out


def _apply_qr_crosscheck(invoice: Invoice, payload: str) -> None:
    """Đối chiếu QR HĐ điện tử với total/vendor đã trích:
    - khớp → tăng confidence thật (total +0.25, vendor +0.1, cap 1.0);
    - total OCR trống → lấy từ QR;
    - total MISMATCH → QR thắng (dữ liệu máy sinh, chống đọc sai OCR) — chỉ khi
      parse theo key (trusted); chuỗi | heuristic chỉ đối chiếu, không đổi gì."""
    qr = _parse_qr_payload(payload)
    boost = 0.0
    qt = qr["total"]
    if qt is not None:
        if not invoice.total:
            invoice.total = qt  # OCR không đọc được total → QR
            boost += 0.25
        elif abs(invoice.total - qt) <= max(1.0, invoice.total * 0.005):
            boost += 0.25  # 2 nguồn khớp → OCR đúng, tin tưởng hơn
        elif qr["trusted"]:
            invoice.total = qt  # MISMATCH → QR dữ liệu máy sinh thắng OCR
        else:
            qt = None  # mismatch + heuristic (chuỗi | không key) → không biết bên nào sai
        if qt is not None:
            # QR xác nhận/ghi đè → field này coi như verified (ô xanh trên UI review)
            invoice.provenance["total"] = FieldProvenance(
                value=invoice.total, confidence=1.0, source="qr", evidence=payload[:200])
    qv = qr["vendor"]
    if qv:
        if invoice.vendor in ("", "unknown"):
            invoice.vendor = qv
            invoice.provenance["vendor"] = FieldProvenance(
                value=qv, confidence=1.0, source="qr", evidence=payload[:200],
            )
            boost += 0.1
        else:
            a, b = _norm_name(qv), _norm_name(invoice.vendor)
            if a and b and (a in b or b in a):
                boost += 0.1  # vendor khớp (fuzzy) → thêm 1 nhịp
                invoice.provenance["vendor"] = FieldProvenance(
                    value=invoice.vendor, confidence=1.0, source="qr", evidence=payload[:200])
    if qr["tax_id"] and not invoice.supplier_tax_id:
        invoice.supplier_tax_id = qr["tax_id"]
        invoice.provenance["supplier_tax_id"] = FieldProvenance(
            value=qr["tax_id"], confidence=1.0, source="qr", evidence=payload[:200])
    if qr["invoice_number"] and invoice.invoice_number in ("", "unknown"):
        invoice.invoice_number = qr["invoice_number"]
        invoice.provenance["invoice_number"] = FieldProvenance(
            value=qr["invoice_number"], confidence=1.0, source="qr", evidence=payload[:200])
    if boost:
        invoice.confidence = min(1.0, invoice.confidence + boost)


import io

# --- Helpers for backward compatibility with existing tests ---

def _norm_name(s: str) -> str:
    """Chuẩn hóa tên: lower, bỏ dấu, bỏ space — dùng để so sánh fuzzy."""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _to_float(s: str, vi: bool = False) -> float:
    """Parse số tiền theo 2 quy ước — vi=True: dấu phẩy là thập phân, dấu chấm là nghìn
    (VN/EU/KR: 30.900.000); vi=False: dấu chấm là thập phân (US: 1,234.56).
    Cờ locale chỉ dùng cho token PHÂN VÂN 1 nhóm 3 số ('1.500': vi=True → 1500,
    vi=False → 1.5); token có cả 2 dấu, nhóm 3 nhiều nhóm, 0.xxx đều tự quyết định."""
    s = s.strip().replace(" ", "")
    # Cả 2 dấu → dấu đứng CUỐI là thập phân (unambiguous, không cần cờ locale)
    if "," in s and "." in s:
        if s.rindex(",") < s.rindex("."):
            return float(s.replace(",", ""))               # 1.234,56 → 1234.56
        return float(s.replace(".", "").replace(",", "."))  # 1,234.56 → 1234.56
    # 0.xxx / 0,xxx: số 0 đầu +3 chữ số lẻ là thập phân (0.125 ≠ 125)
    if re.match(r"^0[.,]\d", s):
        return float(s.replace(",", "."))
    if "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 3 and len(parts) > 1:
            return float(s.replace(",", ""))   # 1,591,600 / 1,500 → nghìn
        return float(s.replace(",", "."))      # 177,20 → 177.20
    if "." in s:
        if re.match(r"^\d{1,3}(\.\d{3})+$", s):
            # Nhiều nhóm ('1.591.600') luôn là nghìn; 1 nhóm ('1.500') phân vân
            # → theo cờ: dot-thousands → 1500, receipt Mỹ → 1.5
            if vi or s.count(".") > 1:
                return float(s.replace(".", ""))
        return float(s)
    return float(s)


def _detect_comma_decimal(text: str) -> bool:
    """Đoán quy ước số của TOÀN receipt: True nếu dấu phẩy là thập phân (VN/EU/KR).
    Bỏ phiếu trên mọi số trong text: token có 2 dấu → dấu cuối là thập phân;
    nhóm 3 ('61.500'/'1,500') → dấu đó là NGÀN (bằng chứng mạnh, ×2);
    '2.50' → dấu là thập phân. Hòa → theo ngôn ngữ (_VI_DETECT); không đủ → US."""
    vote_comma = 0  # phẩy là thập phân (dot-thousands)
    vote_dot = 0    # chấm là thập phân (comma-thousands)
    for tok in _NUM_TOKEN_RE.findall(text):
        if "," in tok and "." in tok:
            if tok.rindex(",") < tok.rindex("."):
                vote_dot += 1   # 1,234.56
            else:
                vote_comma += 1  # 1.234,56
            continue
        sep = "," if "," in tok else ("." if "." in tok else None)
        if sep is None:
            continue
        head, trailing = tok.rsplit(sep, 1)
        if head == "0":
            # 0.125 → thập phân
            if sep == ",":
                vote_comma += 1
            else:
                vote_dot += 1
        elif len(trailing) == 2:
            # kiểu tiền in 2 số lẻ: 12.50 / 177,20 → dấu đó là thập phân
            if sep == ",":
                vote_comma += 1
            else:
                vote_dot += 1
        elif re.match(r"^\d{1,3}(?:[.,]\d{3})+$", tok):
            # nhóm 3 chuẩn: dấu đó là NGÀN → vote ngược; ×2 vì in đúng 3 chữ số
            # sau dấu là bằng chứng mạnh (61.500 → phẩy là thập phân)
            if sep == ",":
                vote_dot += 2
            else:
                vote_comma += 2
    if vote_comma != vote_dot:
        return vote_comma > vote_dot
    return bool(_VI_DETECT.search(text))


_MONTH_NUM = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _normalize_date(s: str) -> str:
    """Chuẩn hóa date về YYYY-MM-DD — nhận DD-MM-YYYY, YYYY-MM-DD, DD MON YYYY,
    DD.MM.YY, YYYYMMDD/DDMMYYYY (GT receipt thật in đủ kiểu; không chuẩn hóa được
    thì so sánh GT tính sai — extractor đúng vẫn bị coi là fail)."""
    s = s.strip()
    m = re.match(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", s)
    if m:
        d, mo, y = m.groups()
        if 1 <= int(d) <= 31 and 1 <= int(mo) <= 12:
            if len(y) == 2:
                y = "20" + y
            return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(d) <= 31 and 1 <= int(mo) <= 12:
            return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    m = re.match(r"(\d{1,2})[-/\s]([A-Za-z]{3,9})[-/\s](\d{2,4})$", s)
    if m:
        d, mon, y = m.groups()
        mo = _MONTH_NUM.get(mon.lower()[:3], 0)
        if mo:
            if len(y) == 2:
                y = "20" + y
            return f"{y}-{mo:02d}-{int(d):02d}"
    m = re.match(r"(\d{8})$", s)
    if m:
        y, mo, d = s[:4], s[4:6], s[6:]
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
        d, mo, y = s[:2], s[2:4], s[4:]  # DDMMYYYY
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    return s


def _guess_vendor(text: str) -> str:
    """Đoán vendor từ text — dùng VENDOR_RE hoặc tìm chain name."""
    m = _VENDOR_RE.search(text)
    if m:
        return m.group(1).strip()
    chain = _find_chain_in_text(text)
    if chain:
        return chain
    return "unknown"


def _find_chain_in_text(text: str) -> Optional[str]:
    """Tìm tên chuỗi cửa hàng trong text."""
    text_lower = text.lower()
    for chain in _VN_CHAINS:
        if chain.lower() in text_lower:
            return chain
    return None


_VN_CHAINS = (
    "VinMart", "Circle K", "FamilyMart", "Ministop", "7-Eleven",
    "GS25", "CU", "Emart", "Lotte Mart", "Big C", "Tops",
)


_NUM_TOKEN_RE = re.compile(r"\d[\d.,]*")


def _grounded(fields: dict, text: str) -> dict:
    """Chống hallucinate: vendor/total do LLM trả về phải xuất hiện trong text gốc."""
    out = dict(fields)
    v = out.get("vendor")
    if v:
        nv = _norm_name(str(v))
        lines = [_norm_name(l) for l in text.splitlines() if len(l.strip()) >= 4]
        ok = bool(nv) and (nv in "".join(lines) or any(
            difflib.SequenceMatcher(None, nv, l).ratio() >= 0.85 for l in lines if l))
        if not ok:
            out.pop("vendor")
    t = out.get("total")
    if t is not None:
        nums = set()
        for tok in _NUM_TOKEN_RE.findall(text):
            for vi in (False, True):
                try:
                    nums.add(round(_to_float(tok, vi), 2))
                except (ValueError, OverflowError):
                    pass
        # also try parsing t itself (may contain commas) via _to_float
        t_vals = set()
        try:
            t_vals.add(float(str(t).replace(",", "")))
        except Exception:
            pass
        for vi in (False, True):
            try:
                t_vals.add(_to_float(str(t), vi))
            except Exception:
                pass
        if t_vals:
            if not any(any(abs(tv - n) < 0.01 for n in nums) for tv in t_vals):
                out.pop("total")
        else:
            try:
                if not any(abs(float(str(t).replace(",", "")) - n) < 0.01 for n in nums):
                    out.pop("total")
            except Exception:
                out.pop("total")
    return out


def _merge_fields(regex_fields: dict, llm_fields: dict) -> dict:
    """Regex là nguồn chính; LLM lấp chỗ regex bỏ sót (unknown/None/0)."""
    merged = dict(regex_fields)
    for k, v in llm_fields.items():
        if v is None or v == "" or v == "unknown":
            continue
        cur = merged.get(k)
        if cur in (None, "", "unknown", 0, 0.0):
            merged[k] = v
    return merged


_VI_DETECT = re.compile(
    r"số\s*h[oó][aá]\s*đơn|tổng\s*cộng|thuế\s*gtgt|người\s*bán|đồng|mst"
    r"|đơn\s*vị\s*bán|ngày\s*lập|tổng\s*phải\s*trả|giá\s*trị", re.I)
_CURRENCY_RE = re.compile(r"(USD|EUR|VND|GBP|JPY)", re.I)


def _extract_regex(text: str, layout: Optional[list] = None) -> tuple:
    """Regex extraction — trả về (fields_dict, field_count, prov).

    prov: field → (confidence, evidence) — confidence theo ĐƯỜNG bắt được field:
    nhãn rõ ràng (Vendor:/Số hóa đơn:) tin hơn fallback dòng đầu / ngày mơ hồ,
    vì fallback chính là nhóm hay trích sai trên receipt thật.
    layout: bbox OCR (nếu có) để chọn total theo vị trí."""
    vi = bool(_VI_DETECT.search(text))
    comma = _detect_comma_decimal(text)  # quy ước số theo ngữ cảnh receipt (2 quy ước)
    fields = {}
    prov = {}
    count = 0

    m = _INVOICE_NO_RE.search(text)
    if m:
        fields["invoice_number"] = m.group(1).strip()
        prov["invoice_number"] = (0.9, m.group(0))
        count += 1

    m = _VENDOR_RE.search(text)
    if m:
        cand = m.group(1).strip()
        if len(cand) < 60 and "date of purchase" not in cand.lower() and "request tax" not in cand.lower():
            fields["vendor"] = _clean_vendor_line(cand)
            prov["vendor"] = (0.85, m.group(0))
            count += 1
        else:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if lines:
                fields["vendor"] = _clean_vendor_line(lines[0][:80])
                prov["vendor"] = (0.5, lines[0])
                count += 1
    else:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            first = lines[0]
            if len(first) >= 3 and not re.match(r"^(receipt|invoice|tax invoice|cash|date|doc no)", first, re.I):
                fields["vendor"] = _clean_vendor_line(first[:80])
                prov["vendor"] = (0.5, first)  # dòng đầu — heuristic, không phải nhãn
                count += 1
    m = _DATE_RE.search(text)
    if m:
        fields["issue_date"] = _normalize_date(m.group(1))
        prov["issue_date"] = (0.85, m.group(0))
        count += 1
    else:
        # Fallback: tìm ngày dạng 25 MAY 2017 hoặc DD-MM-YY hoặc DD/MM/YYYY
        dm = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", text)
        if dm:
            try:
                import datetime as _dt
                _months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                _mo = _months.get(dm.group(2).lower()[:3], 0)
                if _mo:
                    fields["issue_date"] = f"{dm.group(3)}-{_mo:02d}-{int(dm.group(1)):02d}"
                    prov["issue_date"] = (0.7, dm.group(0))
                    count += 1
            except: pass
        if "issue_date" not in fields:
            gm = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", text)
            if gm:
                fields["issue_date"] = _normalize_date(gm.group(1))
                prov["issue_date"] = (0.5, gm.group(0))  # không nhãn → dễ bắt nhầm ngày khác
                count += 1

    m = _DUE_RE.search(text)
    if m:
        fields["due_date"] = _normalize_date(m.group(1))
        prov["due_date"] = (0.85, m.group(0))
        count += 1

    total_str = _pick_total(text, layout=layout)
    if total_str:
        fields["total"] = _to_float(total_str, vi=comma)
        prov["total"] = (0.9, total_str)
        count += 1

    # Tax — tổng tất cả dòng thuế (GTGT + tiêu thụ đặc biệt + ...), loại "thuế suất" (rate)
    tax_re = re.compile(
        r"(?:thuế\s*gtgt|thuế(?!\s*suất)|\btax\b|\bvat\b|\bgst\b)(?!\s*(?:id|reg|no|#|code|summary|rate))"
        r"(?:(?!\s*[:#]?\s*[0-9.,]*\s*%).)*?"
        r"\s*[:#]?\s*(?:rm|usd|eur|vnd|gbp|jpy|myr|\$)?\s*([0-9]+(?:[.,][0-9]+)*)",
        re.I,
    )
    tax_matches = list(tax_re.finditer(text))
    if tax_matches:
        fields["tax"] = sum(_to_float(m.group(1), vi=comma) for m in tax_matches)
        prov["tax"] = (0.8, " | ".join(m.group(0) for m in tax_matches))
        count += 1

    # Currency — dựa trên nội dung tiếng Việt hoặc flag rõ ràng
    m = _CURRENCY_RE.search(text)
    fields["currency"] = m.group(1) if m else ("VND" if vi else "USD")
    # có token tiền tệ rõ ràng → tin; suy từ ngôn ngữ → đoán (ô vàng trên UI review)
    prov["currency"] = (0.8, m.group(0)) if m else (0.5, None)

    return fields, count, prov


def _make_id(invoice_no: str, source_file: str) -> str:
    """Tạo ID duy nhất cho invoice."""
    import hashlib
    base = f"{invoice_no}:{source_file}"
    return hashlib.md5(base.encode()).hexdigest()[:12]


def _coerce_num(v, vi: bool):
    """Coerce LLM numeric string (with comma/dot) to float, honoring vi flag."""
    if v is None or v == "" or v == "unknown":
        return v
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    # try vi-aware then fallback
    for use_vi in (vi, not vi):
        try:
            return _to_float(s, vi=use_vi)
        except Exception:
            pass
    try:
        return float(s.replace(",", "").replace(" ", ""))
    except Exception:
        return v


# --- Line items: bảng hàng hóa (Mã SP / SL / Đơn giá / Thành tiền) ---
_ITEM_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
# Header so khớp trên dòng BỎ DẤU: cần cột mô tả + SL + (đơn giá hoặc thành tiền)
_ITEM_DESC_RE = re.compile(
    r"\bma\s*sp\b|\bma\s*hang\b|\bmat\s*hang\b|\bhang\s*hoa\b|ten\s*hang|dien\s*giai"
    r"|\bitem\b|\bproduct\b|\bdesc|\bgoods\b", re.I)
_ITEM_QTY_RE = re.compile(r"\bSL\b|so\s*luong|\bqty|\bquantity", re.I)
_ITEM_PRICE_RE = re.compile(r"don\s*gia|unit\s*price|\bprice|gia\s*ban", re.I)
_ITEM_AMT_RE = re.compile(
    r"thanh\s*tien|tien\s*hang|cong\s*tien|\bamount\b|\bextended\b|\btotal\b", re.I)
# Dừng bảng ở dòng tổng/thuế/thanh toán — check TRƯỚC khi parse:
# dòng "Tổng cộng ... 150.000" có khi đủ 3 số, parse sẽ ra item giả.
_ITEM_STOP_RE = re.compile(
    r"sub\s*total|\btotal\b|tong\s*cong|tong\s*tien|tong\s*phai\s*tra|thanh\s*toan"
    r"|\bcash\b|\bpayment\b|\bvat\b|thue\s*gtgt|\btax\b|discount|giam\s*gia"
    r"|\bbalance\b|\bchange\b|tien\s*thoi", re.I)


def _extract_line_items(text: str, layout: Optional[list] = None) -> list:
    """Bắt bảng hàng hóa: dòng header → các dòng hàng (3 số cuối = SL, Đơn giá,
    Thành tiền). Dòng phải đạt SL × Đơn giá ≈ Thành tiền (dung sai 3%) mới lấy —
    chống bắt nhầm số điện thoại/ngày ở footer. Không đụng count/confidence."""
    lines = [ln.text for ln in layout] if layout else text.splitlines()
    vi = _detect_comma_decimal(text)  # cùng quy ước số với total
    items = []
    header_seen = False
    for raw in lines:
        line = _strip_accents(raw)
        if not header_seen:
            header_seen = bool(
                _ITEM_DESC_RE.search(line) and _ITEM_QTY_RE.search(line)
                and (_ITEM_PRICE_RE.search(line) or _ITEM_AMT_RE.search(line))
            )
            continue
        if not line.strip():
            continue
        if _ITEM_STOP_RE.search(line):
            break
        m = list(_ITEM_NUM_RE.finditer(raw))
        if len(m) < 3:
            continue  # thiếu cột số
        # 3 số CUỐI dòng = SL, đơn giá, thành tiền; phần trước = tên hàng
        desc = raw[:m[-3].start()].strip(" \t-–:.,|")
        if not desc:
            continue
        try:
            qty = _to_float(m[-3].group(), vi)
            price = _to_float(m[-2].group(), vi)
            amount = _to_float(m[-1].group(), vi)
        except ValueError:
            continue
        if not (0 < qty <= 10000) or price < 0 or amount <= 0:
            continue
        if abs(qty * price - amount) > max(1.0, amount * 0.03):
            continue  # SL × đơn giá ≠ thành tiền → không phải dòng hàng
        items.append(LineItem(
            description=desc,
            quantity=qty,
            unit_price=price,
            amount=amount,
            provenance=FieldProvenance(
                value=amount, confidence=0.9, source="regex",
                evidence=raw.strip()[:200],
            ),
        ))
    return items


def extract_from_text(text: str, source_file: str = "", llm=None, layout: Optional[list] = None) -> Invoice:
    """Trích xuất Invoice. Regex trước; nếu confidence < 0.8 và có LLM → merge.
    LLM_MODE=primary: LLM (đã qua grounding) GHI ĐÈ vendor/date/total của regex.
    layout (list[OcrLine], có khi text sinh từ OCR): chọn total theo vị trí bbox."""
    fields, count, prov = _extract_regex(text, layout=layout)
    confidence = min(1.0, count / 5.0)
    mode = os.getenv("LLM_MODE", "fill")
    comma = _detect_comma_decimal(text)  # quy ước số theo ngữ cảnh — cho coerce LLM

    if llm is not None and confidence < _llm_threshold():
        llm_fields = _extract_llm(text, llm)
        # coerce numeric strings before grounding comparison
        for k in ("total", "tax", "discount"):
            if k in llm_fields:
                llm_fields[k] = _coerce_num(llm_fields[k], comma)
        llm_fields = _grounded(llm_fields, text)
        # also ensure after grounded any remaining string numbers coerced
        for k in ("total", "tax", "discount"):
            if k in llm_fields and isinstance(llm_fields[k], str):
                llm_fields[k] = _coerce_num(llm_fields[k], comma)
        regex_fields = dict(fields)
        if mode == "primary":
            for k in ("vendor", "issue_date", "due_date", "total"):
                if k in llm_fields and llm_fields[k] not in (None, "", "unknown"):
                    fields[k] = llm_fields[k]
        else:
            fields = _merge_fields(fields, llm_fields)
        # after successful LLM merge, boost confidence
        if llm_fields:
            confidence = 1.0
            # ensure tax/total are numeric floats
            for k in ("total", "tax"):
                if k in fields and isinstance(fields[k], str):
                    fields[k] = _coerce_num(fields[k], comma)
        # field nào LLM điền/ghi đè → provenance nguồn "llm" (0.85)
        for k in fields:
            if k in llm_fields and fields[k] != regex_fields.get(k):
                prov[k] = (0.85, None, "llm")

    # final coerce for fields that may still be strings (e.g. regex fallback shouldn't, but LLM path may)
    for k in ("total", "tax"):
        if k in fields and isinstance(fields[k], str):
            fields[k] = _coerce_num(fields[k], comma)

    # Provenance từng field: confidence theo đường trích xuất → UI tô đỏ ô yếu
    provenance = {}
    for k, p in prov.items():
        if k not in fields:
            continue
        conf, ev = p[0], p[1]
        src = p[2] if len(p) > 2 else "regex"
        provenance[k] = FieldProvenance(
            value=fields[k], confidence=conf, source=src, text_span=ev,
        )

    invoice = Invoice(
        id=_make_id(fields.get("invoice_number", "unknown"), source_file),
        invoice_number=fields.get("invoice_number", "unknown"),
        vendor=fields.get("vendor", "unknown"),
        issue_date=fields.get("issue_date"),
        due_date=fields.get("due_date"),
        total=fields.get("total", 0.0),
        tax=fields.get("tax", 0.0),
        currency=fields.get("currency", "USD"),
        source_file=source_file,
        line_items=_extract_line_items(text, layout),
        provenance=provenance,
        confidence=confidence,
    )
    return invoice


_LLM_SYSTEM = """Bạn trích xuất thông tin từ hóa đơn. Trả về JSON thuần (không markdown, không giải thích) với keys:
invoice_number, vendor, issue_date, due_date, total, tax, currency.
Chỉ trả JSON."""


def _extract_llm(text: str, provider) -> dict:
    """Gọi LLM để trích xuất fields."""
    try:
        raw = provider.complete(_LLM_SYSTEM, text)
        # Strip markdown code block if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception:
        return {}
