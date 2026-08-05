"""Batch extractor — xử lý nhiều hóa đơn song song, cách ly lỗi."""
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .extractor import extract_invoice


def extract_batch(files, repo, user_id: str = "", max_workers: int = 4) -> dict:
    """Trích xuất hàng loạt. files = list (filename, bytes). repo = InvoiceRepository.

    Mỗi file chạy thread riêng; file lỗi bị skip (log error), file thành công
    được upsert. Trả về: {total, successful, failed, results, errors}.
    """
    def _one(item):
        filename, data = item
        suffix = Path(filename).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            f.write(data)
            path = f.name
        try:
            inv = extract_invoice(path)
            return filename, inv, None
        except Exception as e:  # noqa: BLE001 — cách ly lỗi từng file
            return filename, None, f"{type(e).__name__}: {e}"
        finally:
            Path(path).unlink(missing_ok=True)

    results, errors = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_one, it): it[0] for it in files}
        for fut in as_completed(futs):
            filename, inv, err = fut.result()
            if err:
                errors.append({"file": filename, "error": err})
            else:
                inv.user_id = user_id
                repo.upsert(inv)
                results.append(inv)

    return {
        "total": len(files),
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }