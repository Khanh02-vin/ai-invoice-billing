# invoice-ai

Đây là project độc lập cho phần AI trích xuất hóa đơn của Invoice Billing. Project không sở hữu auth, database, file storage, billing hoặc Celery/Redis.

## Cài đặt

```bash
cd ai-engine
python -m pip install -e '.[dev,llm]'
```

OCR là tùy chọn:

```bash
python -m pip install -e '.[ocr-tesseract]'
# Linux: sudo apt-get install tesseract-ocr
```

PaddleOCR nằm trong extra `ocr-paddle` vì có runtime/model lớn.

## Sử dụng

```python
from invoice_ai import extract_invoice, extract_from_text

invoice = extract_from_text("""INVOICE\nInvoice No: INV-001\nVendor: Example Ltd\nTotal: $1,000.00""")
invoice = extract_invoice(pdf_or_image_bytes, "application/pdf", source_file="invoice.pdf")
```

Kết quả là Pydantic `Invoice`, trong đó `provenance` lưu confidence, nguồn (`regex`, `ocr`, `llm`) và bằng chứng cho từng trường.

## Cấu hình LLM

LLM chỉ được gọi khi provider khả dụng và extractor cần bổ sung dữ liệu. Có thể dùng mọi endpoint tương thích OpenAI:

- `LLM_BASE_URL` (mặc định endpoint Qwencoder hiện tại)
- `LLM_API_KEY` hoặc `OPENAI_API_KEY`
- `LLM_MODEL`
- `LLM_MODE=fill` hoặc `primary`

Mặc định không cần API key và pipeline vẫn chạy regex/PDF/OCR offline. Không gửi dữ liệu ra ngoài nếu chưa cấu hình key/provider.

## Tích hợp billing app

Billing app giữ quyền sở hữu persistence và workflow. Khi đóng gói project này, app có thể cài `invoice-ai` rồi đổi import từ `src.extract`/`src.llm` sang `invoice_ai.extract`/`invoice_ai.llm`; không truyền repository hoặc database vào core extraction.
