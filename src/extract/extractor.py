"""Trích xuất trường hóa đơn từ file.
Hỗ trợ: tiếng Anh + Hóa đơn GTGT điện tử tiếng Việt + ảnh scan (OCR).
Regex chính; LLM fallback khi regex đọc thiếu (confidence < 0.8).
ponytail: chuỗi OCR Paddle→Tesseract, LLM qua provider có thể inject."""
import json
import os
import re
from typing import Optional
from ..domain.models import Invoice
from ..llm.base import get_llm_provider, LLMProvider

# --- Nhãn song ngữ (Anh + Việt) ---
_INVOICE_NO_RE = re.compile(
    r"(?:invoice\s*(?:no|number|#)|số\s*h[oó][aá]\s*đơn|hđ\s*số)\s*[:#]?\s*([A-Z0-9\-_/]+)", re.I)
_VENDOR_RE = re.compile(
    r"(?:from|vendor|seller|supplier|người\s*bán(?!\s*hàng))\s*[:#]?\s*(.+)", re.I)
_DATE_RE = re.compile(
    r"(?:invoice\s*date|issue\s*date|dated|ngày)\s*[:#]?\s*"
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.I)
_DUE_RE = re.compile(
    r"(?:due\s*date|payment\s*due)\s*[:#]?\s*"
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.I)
# "cộng tiền hàng hóa" (subtotal) bị loại bằng lookahead; ưu tiên "tổng cộng tiền thanh toán"
_AMOUNT_RE = re.compile(
    r"(?:tổng\s*cộng(?:\s*tiền\s*thanh\s*toán)?|grand\s*total|\btotal\b|cộng\s*tiền(?!\s*hàng)|amount\s*(?:due)?)"
    r"\s*[:#]?\s*\$?\s*([0-9.,]+)", re.I)
# "Thuế suất GTGT: 10%" bị loại (lookahead suất + %); findall cộng dồn nhiều mức thuế
_TAX_RE = re.compile(
    r"(?:thuế\s*gtgt|thuế(?!\s*suất)|\btax\b|vat)"
    r"(?!\s*[:#]?\s*\$?\s*[0-9.,]*\s*%)\s*[:#]?\s*\$?\s*([0-9.,]+)", re.I)
# Chiết khấu: "Chiết khấu thương mại: 1,000,000". Loại giá trị dạng %.
_DISCOUNT_RE = re.compile(
    r"(?:chiết\s*khấu(?:\s*thương\s*mại)?|discount)(?!\s*[:#]?\s*\$?\s*[0-9.,]*\s*%)"
    r"[^0-9]*?([0-9.,]+)", re.I)
_CURRENCY_RE = re.compile(r"(USD|EUR|VND|GBP|JPY)", re.I)
_VI_DETECT = re.compile(
    r"số\s*h[oó][aá]\s*đơn|tổng\s*cộng|thuế\s*gtgt|người\s*bán|đồng|mst"
    r"|đơn\s*vị\s*bán|ngày\s*lập|tổng\s*phải\s*trả|giá\s*trị", re.I)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tiff", ".bmp")


def read_file_text(path: str) -> str:
    """Đọc text từ file PDF, ảnh (OCR), hoặc text."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext in _IMAGE_EXTS:
        return _read_image(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _read_pdf(path: str) -> str:
    """Đọc PDF, thử pdfplumber rồi PyPDF2."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        try:
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                return "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception:
            return ""


def _try_paddle(path: str) -> str:
    """OCR bằng PaddleOCR (tiếng Việt)."""
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(lang="vi", show_log=False)
        result = ocr.ocr(path)
        lines = []
        for page in result if result else []:
            for line in page or []:
                lines.append(line[1][0])
        return "\n".join(lines)
    except Exception:
        return ""


def _try_tesseract(path: str) -> str:
    """OCR bằng Tesseract (tiếng Việt)."""
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(path), lang="vie")
    except Exception:
        return ""


def _read_image(path: str) -> str:
    """OCR ảnh: PaddleOCR → Tesseract → rỗng."""
    for fn in (_try_paddle, _try_tesseract):
        text = fn(path)
        if text.strip():
            return text
    return ""


def _to_float(s: str, vi: bool) -> float:
    """Đọc số tiền. VND dùng phẩy/chấm làm ngăn nghìn, không có thập phân.
    Nếu non-vi mà vẫn có dạng VND (1.000.000) → thử bỏ cả hai."""
    s = s.strip()
    if vi:
        return float(s.replace(",", "").replace(".", ""))
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return float(s.replace(",", "").replace(".", ""))


def _normalize_date(s: str) -> str:
    """Chuẩn hóa ngày về ISO yyyy-mm-dd.
    Việt Nam dd/mm/yyyy, quốc tế yyyy-mm-dd — báo cáo tháng cần ISO."""
    parts = re.split(r"[-/]", s)
    if len(parts[0]) == 4:  # yyyy-mm-dd
        y, m, d = parts
    else:  # dd/mm/yyyy
        d, m, y = parts
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def extract_invoice(path: str, llm: Optional[LLMProvider] = None) -> Invoice:
    """Trích xuất Invoice từ file. Tự dùng LLM fallback nếu có API key."""
    text = read_file_text(path)
    if llm is None:
        llm = get_llm_provider()
    return extract_from_text(text, source_file=path, llm=llm)


def _extract_regex(text: str) -> tuple:
    """Trích xuất bằng regex. Trả về (dict trường, số trường tìm thấy)."""
    vi = bool(_VI_DETECT.search(text))
    f = {}
    count = 0

    m = _INVOICE_NO_RE.search(text)
    f["invoice_number"] = m.group(1) if m else "unknown"
    count += 1 if m else 0

    m = _VENDOR_RE.search(text)
    f["vendor"] = m.group(1).strip().splitlines()[0][:60] if m else "unknown"
    count += 1 if m else 0

    m = _DATE_RE.search(text)
    f["issue_date"] = _normalize_date(m.group(1)) if m else None
    count += 1 if m else 0

    m = _DUE_RE.search(text) if not vi else None
    f["due_date"] = _normalize_date(m.group(1)) if m else None
    # due_date không tính vào confidence — GTGT Việt Nam không có trường này

    m = _AMOUNT_RE.search(text)
    f["total"] = _to_float(m.group(1), vi) if m else 0.0
    count += 1 if m else 0

    tax = sum(_to_float(m, vi) for m in _TAX_RE.findall(text))
    f["tax"] = tax
    count += 1 if tax else 0

    f["discount"] = sum(_to_float(m, vi) for m in _DISCOUNT_RE.findall(text))

    m = _CURRENCY_RE.search(text)
    f["currency"] = m.group(1) if m else ("VND" if vi else "USD")
    return f, count


_LLM_SYSTEM = """Bạn trích xuất thông tin từ hóa đơn. Trả về JSON thuần (không markdown, không giải thích) với keys:
invoice_number, vendor, issue_date (dạng yyyy-mm-dd), total (số), tax (số), discount (số), currency.
Thiếu trường nào dùng null. Chỉ trả JSON."""


def _extract_llm(text: str, provider: LLMProvider) -> dict:
    """Trích xuất bằng LLM → dict trường. Rỗng nếu lỗi."""
    try:
        raw = provider.complete(_LLM_SYSTEM, f"Hóa đơn:\n{text[:3000]}")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        data = json.loads(raw)
        result = {}
        for k in ("invoice_number", "vendor", "issue_date", "due_date",
                  "total", "tax", "discount", "currency"):
            if k not in data or data[k] is None:
                continue
            v = data[k]
            if k in ("total", "tax", "discount"):
                try:
                    v = float(str(v).replace(",", "").replace(" ", ""))
                except (ValueError, TypeError):
                    continue
            elif k in ("issue_date", "due_date"):
                try:
                    v = _normalize_date(str(v))
                except Exception:
                    continue  # không phải ngày → bỏ, giữ giá trị regex
            result[k] = v
        return result
    except Exception:
        return {}


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


def extract_from_text(text: str, source_file: str = "", llm: Optional[LLMProvider] = None) -> Invoice:
    """Trích xuất Invoice. Regex trước; nếu confidence < 0.8 và có LLM → merge."""
    fields, count = _extract_regex(text)
    confidence = min(1.0, count / 5.0)

    if confidence < 0.8 and llm is not None:
        llm_fields = _extract_llm(text, llm)
        if llm_fields:
            fields = _merge_fields(fields, llm_fields)
            core = ["invoice_number", "vendor", "issue_date", "total", "tax"]
            new_count = sum(1 for k in core if fields.get(k) not in (None, "", "unknown", 0, 0.0))
            confidence = min(1.0, new_count / 5.0)

    snippet = text[:200].replace("\n", " ")
    return Invoice(
        id=_make_id(fields["invoice_number"], source_file),
        invoice_number=fields["invoice_number"],
        vendor=fields["vendor"],
        issue_date=fields.get("issue_date"),
        due_date=fields.get("due_date"),
        currency=fields.get("currency", "USD"),
        total=fields.get("total", 0.0),
        tax=fields.get("tax", 0.0),
        discount=fields.get("discount", 0.0),
        source_file=source_file,
        raw_snippet=snippet,
        confidence=confidence,
    )


def _make_id(invoice_no: str, source_file: str) -> str:
    """Tạo id ổn định từ số hóa đơn + tên file."""
    import hashlib
    key = f"{invoice_no}|{source_file}"
    return hashlib.md5(key.encode()).hexdigest()[:12]