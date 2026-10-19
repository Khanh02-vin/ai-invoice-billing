"""Parse hóa đơn điện tử dạng XML (chuẩn Tổng cục Thuế NĐ123/2020 + TT78/2021).

File XML đính kèm email hóa đơn điện tử VN chứa dữ liệu dạng bảng (cột Ten/MST/
Ngay/TgTTTBSo...) — đọc trực tiếp bằng stdlib ElementTree cho kết quả chính xác
hơn OCR: không lệch dòng, không nhầm ký tự 0/O hay mất chữ thuê (VAT).

Cấu trúc điển hình (mọi nhà cung cấp áp dụng chung schema, chỉ khác namespace):
<HSoThueDTu ... xmlns="urn:cnstabc/...">
  <DLDon><TTChung>...</TTChung></DLDon>
  <NDHDon>
    <TTChung>
      <NBan><Ten>..</Ten><MST>..</MST></NBan>
      <NMua><Ten>..</Ten><MST>..</MST></NMua>
      <THDon><KHMSHDon>K01</KHMSHDon><SHDon>0001234</SHDon><MCCQT>..</MCCQT>
        <Ngay>2026-01-15</Ngay></THDon>
      <DSHHD><HHD><TgTCThue>100000</TgTCThue><TgTThue>10000</TgTThue>
        <TgTTTBSo>110000</TgTTTBSo></HHD></DSHHD>
    </TTChung>
  </NDHDon>
</HSoThueDTu>

Không parse được / không phải hóa đơn → None để caller fallback sang OCR.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from types import SimpleNamespace

logger = logging.getLogger("invoice.xml")


def _localname(tag: str) -> str:
    """Bỏ namespace '{urn:...}Ten' -> 'Ten'."""
    return tag.rsplit("}", 1)[-1]


def _strip_ns(elem: ET.Element) -> ET.Element:
    """Copy cây XML, đổi mọi tag về localname (khỏi phải match namespace thủ công)."""
    out = ET.Element(_localname(elem.tag))
    out.text = elem.text
    out.attrib = {_localname(k): v for k, v in elem.attrib.items()}
    for child in elem:
        out.append(_strip_ns(child))
    return out


def _find(parent: ET.Element, path: str) -> ET.Element | None:
    """Tìm phần tử theo path localname — mỗi bước chỉ xét con trực tiếp."""
    current = parent
    for part in path.split("/"):
        found = next((c for c in current if _localname(c.tag) == part), None)
        if found is None:
            return None
        current = found
    return current


def _find_all(parent: ET.Element, path: str) -> list[ET.Element]:
    """Tìm tất cả phần tử khớp path (duyệt BFS từng tầng)."""
    current = [parent]
    for part in path.split("/"):
        current = [c for node in current for c in node if _localname(c.tag) == part]
    return current


def _text(parent: ET.Element, path: str, default: str = "") -> str:
    node = _find(parent, path)
    return (node.text or "").strip() if node is not None and node.text else default


def _to_float(value) -> float:
    """Parse invoice amounts with Vietnamese/EU thousands and decimal separators."""
    if value is None:
        return 0.0
    text = "".join(c for c in str(value).strip() if c.isdigit() or c in ",.")
    if not text or not any(c.isdigit() for c in text):
        return 0.0

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            integer, decimal = text.rsplit(",", 1)
            text = integer.replace(".", "").replace(",", "") + "." + decimal
        else:
            text = text.replace(",", "")
    elif "," in text:
        integer, decimal = text.rsplit(",", 1)
        if len(decimal) in (1, 2):
            text = integer.replace(",", "").replace(".", "") + "." + decimal
        else:
            text = text.replace(",", "").replace(".", "")
    elif "." in text:
        parts = text.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            text = "".join(parts)
        elif len(parts) > 2:
            text = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_xml_invoice(content: bytes) -> SimpleNamespace | None:
    """Parse XML hóa đơn điện tử → SimpleNamespace với field giống Invoice.

    Trả None khi không đọc được số hóa đơn hoặc tổng tiền — caller fallback OCR.
    """
    if not content or content.lstrip()[:1] != b"<":
        return None
    try:
        root = _strip_ns(ET.fromstring(content))
    except ET.ParseError:
        logger.debug("XML không parse được — bỏ qua, để OCR/LLM xử lý")
        return None

    # 1) Người bán / người mua (schema tổng cục thuế: NBan, NMua)
    vendor = _text(root, "NDHDon/TTChung/NBan/Ten")
    supplier_tax_id = _text(root, "NDHDon/TTChung/NBan/MST")
    buyer = _text(root, "NDHDon/TTChung/NMua/Ten")
    customer_tax_id = _text(root, "NDHDon/TTChung/NMua/MST")

    # 2) Số hiệu: KHMSHDon (ký hiệu mẫu) + SHDon (số) + MCCQT (mã CQT)
    series = _text(root, "NDHDon/TTChung/THDon/KHMSHDon")
    number = _text(root, "NDHDon/TTChung/THDon/SHDon")
    symbol = _text(root, "NDHDon/TTChung/THDon/MCCQT")
    invoice_number = "".join(p for p in (series, symbol, number) if p) or number

    # 3) Ngày phát hành — ISO hoặc dd/mm/yyyy
    issue_date = _text(root, "NDHDon/TTChung/THDon/Ngay")
    if len(issue_date) == 10 and issue_date[2] == "/":
        d, m, y = issue_date[:10].split("/")
        issue_date = f"{y}-{m}-{d}"

    # 4) Số liệu: ưu tiên hàng hóa (DSHHD/HHD); XML dạng tổng hợp lấy ở THDon
    rows = _find_all(root, "NDHDon/TTChung/DSHHD/HHD")
    src = rows[0] if rows else _find(root, "NDHDon/TTChung/THDon")
    if src is None:
        return None
    subtotal = _to_float(_text(src, "TgTCThue"))
    tax = _to_float(_text(src, "TgTThue"))
    total = _to_float(_text(src, "TgTTTBSo"))
    currency = _text(src, "DVTTe") or "VND"

    if not number or total <= 0:
        return None

    return SimpleNamespace(
        invoice_number=invoice_number,
        vendor=vendor or "unknown",
        buyer=buyer,
        supplier_tax_id=supplier_tax_id,
        customer_tax_id=customer_tax_id,
        issue_date=issue_date or None,
        due_date=None,
        currency=currency or "VND",
        subtotal=subtotal,
        tax=tax,
        total=total,
        tax_rate=_to_float(_text(src, "TSThue")),
        discount=_to_float(_text(src, "TgSTThue")),
        amount_due=total,
        # Dữ liệu từ XML chuẩn của cơ quan thuế — UI vẫn cho user review như mọi
        # hóa đơn khác nhưng không đánh dấu "cần kiểm tra gấp".
        confidence=1.0,
        extraction_provider="xml",
    )
