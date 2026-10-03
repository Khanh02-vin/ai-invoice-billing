"""Batch extractor — xử lý nhiều hóa đơn song song, cách ly lỗi."""
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed

from .extractor import extract_invoice
from ..store.repository import DuplicateInvoiceError


def extract_batch(files, repo, user_id: str = "", max_workers: int = 4) -> dict:
    """Trích xuất hàng loạt. files = list (filename, bytes). repo = InvoiceRepository.

    Mỗi file chạy thread riêng; file lỗi bị skip (log error), file thành công
    được upsert. Trả về: {total, successful, failed, results, errors}.
    """
    def _one(item):
        filename, data = item
        try:
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            inv = extract_invoice(
                content=data, mime_type=mime, source_file=filename, user_id=user_id
            )
            return filename, inv, None
        except Exception as e:  # noqa: BLE001 — cách ly lỗi từng file
            return filename, None, f"{type(e).__name__}: {e}"

    results, errors = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_one, it): it[0] for it in files}
        for fut in as_completed(futs):
            filename, inv, err = fut.result()
            if err:
                errors.append({"file": filename, "error": err})
                continue
            try:
                repo.upsert(inv)
                results.append(inv)
            except DuplicateInvoiceError as e:
                # cùng số hóa đơn khác file → 409-style: báo lỗi, không ghi đè
                errors.append({"file": filename, "error": str(e)})

    return {
        "total": len(files),
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }
