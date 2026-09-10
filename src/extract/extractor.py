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
from typing import Optional, Tuple
from ..domain.models import Invoice, FieldProvenance, JobStatus
from ..llm.base import get_llm_provider, LLMProvider

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
        label_low = m.group("label").lower()
        num = m.group("num")
        is_after_rounding = "after rounding" in ctx or "after rounding" in text[max(0,m.start()-30):m.end()].lower()
        is_incl_gst = "incl" in ctx
        if not (is_after_rounding or is_incl_gst):
            if any(k in ctx for k in ("sub", "qty", "quantity", "count", "item", "exclud",
                                      "excl", "tax", "included", "total gst", "adjustment")):
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
        if not (is_incl_gst or is_after_rounding) and label_low.strip() == "total" and any(k in prev_low for k in ("item", "count", "qty", "quantity")):
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


def _parse_number(s: str) -> float:
    """Parse số từ string — hỗ trợ cả dấu chấm và phẩy ngàn (VN: 30.900.000, EU: 1.234,56, US: 1,234.56)."""
    s = s.strip().replace(" ", "")
    if "," in s and "." in s:
        if s.rindex(",") < s.rindex("."):
            return float(s.replace(",", ""))
        else:
            return float(s.replace(".", "").replace(",", "."))
    if "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 3 and len(parts) > 1:
            return float(s.replace(",", ""))
        return float(s.replace(",", "."))
    if "." in s:
        # Only dots: distinguish thousands (30.900.000) vs decimal (177.20)
        import re as _re
        if _re.match(r"^\d{1,3}(\.\d{3})+$", s):
            return float(s.replace(".", ""))
        return float(s)
    return float(s)


def _extract_text(content: bytes, mime_type: str) -> str:
    """Trích xuất text từ file theo MIME type."""
    if mime_type == "application/pdf":
        return _extract_pdf_text(content)
    if mime_type.startswith("image/"):
        return _extract_ocr_text(content)
    if mime_type == "text/plain":
        return content.decode("utf-8", errors="ignore")
    return ""


def _extract_pdf_text(content: bytes) -> str:
    """Trích xuất text từ PDF."""
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


def _extract_ocr_text(content: bytes) -> str:
    """OCR ảnh → text."""
    try:
        from paddleocr import PaddleOCR
        from PIL import Image
        import numpy as np
        ocr = PaddleOCR(use_angle_cls=True, lang="en")
        img = Image.open(io.BytesIO(content))
        result = ocr.ocr(np.array(img), cls=True)
        lines = []
        for line in result[0]:
            lines.append(line[1][0])
        return "\n".join(lines)
    except Exception:
        pass
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
    text = _extract_text(content, mime_type)
    invoice = extract_from_text(text, source_file=source_file)
    invoice.user_id = user_id
    return invoice


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
    """Parse float — vi=True: dấu phẩy là thập phân (kiểu VN), nhưng cũng xử lý dot-thousands."""
    s = s.strip().replace(" ", "")
    if vi:
        # VN: dot = thousands, comma = decimal (30.900.000 or 30.900.000,50)
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            parts = s.split(",")
            if len(parts[-1]) == 3 and len(parts) > 1:
                s = s.replace(",", "")
            else:
                s = s.replace(".", "").replace(",", ".")
        elif "." in s:
            import re as _re2
            if _re2.match(r"^\d{1,3}(\.\d{3})+$", s):
                s = s.replace(".", "")
        return float(s)
    else:
        if "," in s and "." in s:
            if s.rindex(",") < s.rindex("."):
                s = s.replace(",", "")
            else:
                s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            parts = s.split(",")
            if len(parts[-1]) == 3 and len(parts) > 1:
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        elif "." in s:
            import re as _re2
            if _re2.match(r"^\d{1,3}(\.\d{3})+$", s):
                s = s.replace(".", "")
        return float(s)


def _normalize_date(s: str) -> str:
    """Chuẩn hóa date về YYYY-MM-DD."""
    s = s.strip()
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", s)
    if m:
        d, mo, y = m.groups()
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
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


def _extract_regex(text: str) -> tuple:
    """Regex extraction — trả về (fields_dict, field_count)."""
    vi = bool(_VI_DETECT.search(text))
    fields = {}
    count = 0

    m = _INVOICE_NO_RE.search(text)
    if m:
        fields["invoice_number"] = m.group(1).strip()
        count += 1

    m = _VENDOR_RE.search(text)
    if m:
        cand = m.group(1).strip()
        if len(cand) < 60 and "date of purchase" not in cand.lower() and "request tax" not in cand.lower():
            fields["vendor"] = _clean_vendor_line(cand)
            count += 1
        else:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if lines:
                fields["vendor"] = _clean_vendor_line(lines[0][:80])
                count += 1
    else:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            first = lines[0]
            if len(first) >= 3 and not re.match(r"^(receipt|invoice|tax invoice|cash|date|doc no)", first, re.I):
                fields["vendor"] = _clean_vendor_line(first[:80])
                count += 1
    m = _DATE_RE.search(text)
    if m:
        fields["issue_date"] = _normalize_date(m.group(1))
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
                    count += 1
            except: pass
        if "issue_date" not in fields:
            gm = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", text)
            if gm:
                fields["issue_date"] = _normalize_date(gm.group(1))
                count += 1

    m = _DUE_RE.search(text)
    if m:
        fields["due_date"] = _normalize_date(m.group(1))
        count += 1

    total_str = _pick_total(text)
    if total_str:
        fields["total"] = _to_float(total_str, vi=vi)
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
        fields["tax"] = sum(_to_float(m.group(1), vi=vi) for m in tax_matches)
        count += 1

    # Currency — dựa trên nội dung tiếng Việt hoặc flag rõ ràng
    m = _CURRENCY_RE.search(text)
    fields["currency"] = m.group(1) if m else ("VND" if vi else "USD")

    return fields, count


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

def extract_from_text(text: str, source_file: str = "", llm=None) -> Invoice:
    """Trích xuất Invoice. Regex trước; nếu confidence < 0.8 và có LLM → merge.
    LLM_MODE=primary: LLM (đã qua grounding) GHI ĐÈ vendor/date/total của regex."""
    fields, count = _extract_regex(text)
    confidence = min(1.0, count / 5.0)
    mode = os.getenv("LLM_MODE", "fill")
    vi = bool(_VI_DETECT.search(text))

    if llm is not None and confidence < 0.8:
        llm_fields = _extract_llm(text, llm)
        # coerce numeric strings before grounding comparison
        for k in ("total", "tax", "discount"):
            if k in llm_fields:
                llm_fields[k] = _coerce_num(llm_fields[k], vi)
        llm_fields = _grounded(llm_fields, text)
        # also ensure after grounded any remaining string numbers coerced
        for k in ("total", "tax", "discount"):
            if k in llm_fields and isinstance(llm_fields[k], str):
                llm_fields[k] = _coerce_num(llm_fields[k], vi)
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
                    fields[k] = _coerce_num(fields[k], vi)

    # final coerce for fields that may still be strings (e.g. regex fallback shouldn't, but LLM path may)
    for k in ("total", "tax"):
        if k in fields and isinstance(fields[k], str):
            fields[k] = _coerce_num(fields[k], vi)

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
