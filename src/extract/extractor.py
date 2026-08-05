"""Trích xuất trường hóa đơn từ file.
Hỗ trợ: tiếng Anh + Hóa đơn GTGT điện tử tiếng Việt + ảnh scan (OCR).
Regex chính; LLM fallback khi regex đọc thiếu (confidence < 0.8).
ponytail: chuỗi OCR Paddle→Tesseract, LLM qua provider có thể inject."""
import difflib
import json
import os
import re
import unicodedata
from typing import Optional
from ..domain.models import Invoice
from ..llm.base import get_llm_provider, LLMProvider

# --- Nhãn song ngữ (Anh + Việt) ---
_INVOICE_NO_RE = re.compile(
    r"(?:invoice\s*(?:no|number|#)|số\s*h[oó][aá]\s*đơn|hđ\s*số)\s*[:#]?\s*([A-Z0-9\-_/]+)", re.I)
# ponytail: anchored đầu dòng tránh "from the date of purchase..." bắt nhầm trong receipt thật
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
_AMOUNT_RE = re.compile(
    rf"(?P<label>{_TOTAL_LABELS})\s*(?:\(\s*)?"
    rf"(?:incl(?:usive)?[^0-9:#\n]*?(?:\d+(?:[.,]\d+)?%[^0-9:#\n]*?)?"
    rf"|after\s*rounding[^0-9:#\n]*?"
    rf"|[^0-9:#\n]{{0,20}}?)?"
    rf"\s*[:#]?\s*"
    rf"(?:rm|usd|eur|vnd|gbp|jpy|myr|\$)?\s*"
    rf"(?P<num>[0-9]+(?:[.,][0-9]+)*)", re.I)


def _pick_total(text: str) -> Optional[str]:
    """Chọn số total đúng trên receipt thật: loại 'tax total'/subtotal/total qty/count,
    ưu tiên nhãn cụ thể (grand/nett/final/payable/due), hòa nhất bằng dòng total ở dưới cùng.
    Bonus cho dòng mà số là token cuối (loại số giao dịch kiểu 'TOTAL 1010 008 00B0498')."""
    cands = []
    for m in _AMOUNT_RE.finditer(text):
        ctx = m.group(0).lower()
        # bỏ subtotal (kể cả dạng "SUB-TOTAL" gạch nối), "total qty/count/item",
        # "excluding gst", "tax total", "GST @6% INCLUDED IN TOTAL", "TOTAL GST:",
        # "ROUNDING ADJUSTMENT" (chỉ là điều chỉnh, không phải total)
        if any(k in ctx for k in ("sub", "qty", "quantity", "count", "item", "exclud",
                                  "excl", "tax", "included", "total gst", "adjustment")):
            continue
        num = m.group("num")
        if "rounding" in m.group("label").lower():
            val = float(num.replace(",", ""))
            if val < 1.0:
                continue  # "ROUNDING : 0.00" / "ROUNDING 0.02" — chỉ là điều chỉnh, không phải total
        prev = text[max(0, m.start() - 25):m.start()]
        if re.search(r"\b(?:item|count|qty|quantity|no|number|pcs|unit|pos|ref|trans)"
                     r"\s+total\s*$|included\s+in\s*$", prev, re.I):
            continue
        lab = m.group("label").lower()
        rank = 3
        if any(k in lab for k in ("payable", "nett", "grand", "final", "due", "amount",
                                  "rounding")):
            rank = 4
        if "tổng cộng tiền thanh toán" in lab:
            rank = 5
        rest = text[m.end():].split("\n", 1)[0].strip()
        if not rest or re.fullmatch(r"cr|[a-z]{2,3}", rest, re.I):  # số là token cuối dòng
            rank += 1
        cands.append((rank, m.start(), m.group("num")))
    if not cands:
        return None
    best_rank = max(c[0] for c in cands)
    # tie-break: chọn match sau cùng trong text (dòng total ở dưới cùng)
    best = max((c for c in cands if c[0] == best_rank), key=lambda c: c[1])
    return best[2]


def _guess_vendor(text: str) -> str:
    """Fallback cho receipt thật không có label Vendor/Seller: công ty thường ở dòng đầu/đầu hai.
    Bỏ header/giờ/thuế/số tiền/dòng nhãn/địa chỉ. Không áp dụng cho tiếng Việt (có label)."""
    noise = re.compile(
        r"^(receipt|tax invoice|invoice|gst|abn|acn|tel|fax|website|email|"
        r"date|time|cashier|payment|change|thank|like and follow|"
        r"trans|terminal|company no|site|lot|no\.|address|telephone|"
        r"item|qty|price|amount|total|sub.?total|nett?|subtotal|due|"
        r"pre.?auth|page|ref|bill|the|to|goods|posted|jalan|taman|"
        r"số|so|notice|all|any|keep|please|welcome|terima|tq|sale|sales|"
        r"member|card|shop|store|outlet|branch|wisma|menara|blok|unit)",
        re.I)
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 3 or not s[0].isalpha() or s.isdigit():
            continue
        if noise.match(s):
            continue
        if re.fullmatch(r"[\d\s.,$&*%():<>+\-]+", s):  # không có chữ
            continue
        if re.search(r"\d{5,}", s) and not re.search(r"[A-Z]{3,}", s):  # số dài, ko có tên
            continue
        s = re.sub(r"\s*\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                   r"[a-z]*\.?\s+\d{4}(\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$", "", s, flags=re.I)
        s = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*$", "", s)
        s = re.sub(r"\s*tax\s*invoice\s*$", "", s, flags=re.I)
        if not s:
            continue
        return s[:60]
    return "unknown"
# "Thuế suất GTGT: 10%" bị loại (lookahead suất + %); findall cộng dồn nhiều mức thuế
_TAX_RE = re.compile(
    r"(?:thuế\s*gtgt|thuế(?!\s*suất)|\btax\b|vat)"
    r"(?!\s*[:#]?\s*\$?\s*[0-9.,]*\s*%)\s*[:#]?\s*\$?\s*([0-9]+(?:[.,][0-9]+)*)", re.I)
# Chiết khấu: "Chiết khấu thương mại: 1,000,000". Loại giá trị dạng %.
_DISCOUNT_RE = re.compile(
    r"(?:chiết\s*khấu(?:\s*thương\s*mại)?|discount)(?!\s*[:#]?\s*\$?\s*[0-9.,]*\s*%)"
    r"[^0-9]*?([0-9]+(?:[.,][0-9]+)*)", re.I)
_CURRENCY_RE = re.compile(r"(USD|EUR|VND|GBP|JPY)", re.I)
_VI_DETECT = re.compile(
    r"số\s*h[oó][aá]\s*đơn|tổng\s*cộng|thuế\s*gtgt|người\s*bán|đồng|mst"
    r"|đơn\s*vị\s*bán|ngày\s*lập|tổng\s*phải\s*trả|giá\s*trị", re.I)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tiff", ".bmp")

# Chuỗi bán lẻ/ăn uống phổ biến VN — từ điển thương hiệu: sửa vendor khi OCR
# đọc lệch tên hãng (VD "MinComnerce"→VinCommerce, "THE COFFEE HQUSE"→The Coffee House).
_VN_CHAINS = (
    "VinCommerce", "VinMart", "WinMart", "Co.opmart", "Saigon Co.op", "Minimart",
    "Bách Hóa Xanh", "Circle K", "FamilyMart", "GS25", "FPT Shop", "Điện Máy Xanh",
    "Thế Giới Di Động", "Nguyễn Kim", "The Coffee House", "Highlands Coffee",
    "Milano Coffee", "Lotteria", "KFC", "Jollibee", "Pharmacity", "Guardian",
    "Phúc Anh Minimart", "Big C", "Lotte Mart", "AEON", "Mega Market",
)


def _norm_name(s: str) -> str:
    """Chuẩn hóa tên công ty: bỏ hết space + dấu tiếng Việt (OCR lệch space/dấu)."""
    if not s:
        return ""
    s = re.sub(r"\s*\(\s*[\dA-Z-]*-?[\d]+\s*\)\s*$", "", s.strip())
    s = re.sub(r"\s+", "", "".join(
        c for c in unicodedata.normalize("NFD", s.strip(" .,;:/\\\"'()-"))
        if not unicodedata.combining(c)))
    return s.upper()


def _chain_name(s: str) -> str:
    """Trả tên chuỗi nếu vendor khớp (fuzzy ≥0.8) một chuỗi bán lẻ VN trong từ điển.
    Sửa lỗi OCR tên hãng; so khớp ở mức thương hiệu (bỏ tên chi nhánh).
    Áp dụng cho cả 2 ngôn ngữ — vendor English (SROIE) không khớp chuỗi VN nên giữ nguyên."""
    v = _norm_name(s)
    if len(v) < 4:
        return s
    best, best_r = None, 0.0
    for c in _VN_CHAINS:
        r = difflib.SequenceMatcher(None, v, _norm_name(c)).ratio()
        if r > best_r:
            best, best_r = c, r
    return best if best_r >= 0.9 else s


def _find_chain_in_text(text: str):
    """Quét toàn bộ text OCR tìm dòng chứa tên chuỗi bán lẻ VN (fuzzy ≥0.8/dòng).
    Fix thật: vendor line rơi vào tên chi nhánh ("VM+QNH 690 Tran Phu") trong khi
    brand ("VinCommerce") nằm ở dòng khác của receipt."""
    best, best_r = None, 0.0
    for line in text.splitlines():
        s = _norm_name(line.strip())
        if len(s) < 4:
            continue
        for c in _VN_CHAINS:
            r = difflib.SequenceMatcher(None, s, _norm_name(c)).ratio()
            if r > best_r:
                best, best_r = c, r
    return best if best_r >= 0.9 else None


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


_paddle_cache = {}


def _try_paddle(path: str) -> str:
    """OCR bằng PaddleOCR (tiếng Việt). Cache instance — reload mỗi ảnh gây segfault."""
    try:
        if "vi" not in _paddle_cache:
            from paddleocr import PaddleOCR
            _paddle_cache["vi"] = PaddleOCR(lang="vi", show_log=False)
        result = _paddle_cache["vi"].ocr(path)
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
    else:  # dd/mm/yyyy (hoặc dd/mm/yy 2 chữ số trên receipt lâu đời)
        d, m, y = parts
    y = int(y) + 2000 if len(y) == 2 else int(y)  # năm 2 chữ số → 20yy
    return f"{y:04d}-{int(m):02d}-{int(d):02d}"


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
    f["vendor"] = m.group(1).strip().splitlines()[0][:60] if m else (
        _guess_vendor(text) if not vi else "unknown")
    raw_vendor = f["vendor"]
    f["vendor"] = _chain_name(raw_vendor)  # chuẩn hóa tên chuỗi bán lẻ (fix OCR brand)
    if f["vendor"] == raw_vendor:  # chưa khớp chuỗi → quét cả text tìm brand (fix "VM+QNH...")
        full = _find_chain_in_text(text)
        if full:
            f["vendor"] = full
    count += 1 if m else 0

    m = _DATE_RE.search(text)
    # receipt thật không có label ngày → fallback: ngày dạng số đầu tiên trong text (non-vi)
    if m is None and not vi:
        m = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", text)
    f["issue_date"] = _normalize_date(m.group(1)) if m else None
    count += 1 if m else 0

    m = _DUE_RE.search(text) if not vi else None
    f["due_date"] = _normalize_date(m.group(1)) if m else None
    # due_date không tính vào confidence — GTGT Việt Nam không có trường này

    num = _pick_total(text)
    f["total"] = _to_float(num, vi) if num else 0.0
    count += 1 if num else 0

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