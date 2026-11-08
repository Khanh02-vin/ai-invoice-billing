"""Luật nghiệp vụ DÙNG CHUNG khi chấp nhận một kết quả trích xuất là HÓA ĐƠN.

Cả `POST /upload` lẫn `POST /payments/camera-upload` đều trả lời cùng một câu
hỏi liệu tài liệu này có đọc ra được nội dung hóa đơn không, bằng đúng một hàm
dưới đây — trước mỗi endpoint tự đặt "rule" riêng và hai đường cho ra kết quả
khác nhau trên cùng một tài liệu (Sprint 2).

Nguyên tắc miền:

- `invoice_number` là siêu dữ liệu TÙY CHỌN. Nhiều biên lai bán lẻ VN không có
  số hóa đơn; thiếu nó không làm mất giá trị của tài liệu, và không được suy đoán
  số hóa đơn ("unknown" nghĩa là chưa trích xuất được).
- Danh tính bản ghi trong DB là `invoice.id` (UUID). `invoice_number` không bao
  giờ là khoá danh tính và chỉ được dùng để phát hiện trùng lặp (bỏ qua
  "unknown"/"" — xem `InvoiceRepository.upsert`).
- Trường tài chính thật sự bắt buộc: phải đọc ra được ít nhất một số tiền
  (tổng tiền, tiền hàng hoặc dòng hàng). Thiếu toàn bộ số tiền -> tài liệu cần
  chụp lại / nhập tay, không tạo bản ghi "unknown" coi như thành công.
- Không field nào bị vứt bỏ: kết quả trích xuất (kể cả provenance, độ tin cậy)
  được lưu nguyên như worker/camera đang làm.
"""

from typing import Any

# Giá trị đánh dấu "chưa trích xuất được" — extractor sinh ra các giá trị này
# khi không khớp regex/LLM; chúng không khẳng định tài liệu hợp lệ hay không.
UNEXTRACTED = ("", "unknown")


def has_financial_amount(invoice: Any) -> bool:
    """True nếu trích xuất ra được ít nhất một số tiền có nghĩa.

    Dòng hàng cũng chứng minh đây là tài liệu mua bán (biên lai không có dòng
    tổng cộng vẫn giữ được từng dòng hàng).
    """
    if invoice is None:
        return False
    if invoice.line_items and any(
        getattr(item, "amount", 0) > 0 for item in invoice.line_items
    ):
        return True
    return any(
        value and value > 0
        for value in (invoice.total, invoice.subtotal, invoice.amount_due)
    )


def is_unreadable_invoice(invoice: Any) -> bool:
    """True nếu kết quả trích xuất không có nội dung tài chính nào.

    Dùng để quyết định có tạo bản ghi hóa đơn hay không. Khác với rule cũ của
    từng endpoint: `invoice_number` không còn là điều kiện bắt buộc, mà chỉ cần
    đọc ra một số tiền.
    """
    return not has_financial_amount(invoice)
