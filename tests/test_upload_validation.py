"""Endpoint-level regression tests for Sprint 2 — normalize /upload and /camera-upload.

Fixtures (6 classes + unsupported type):
1. RETAIL — VN retail receipt NO invoice number, HAS total → accepted by both
2. GTGT — full e-invoice with number + total → accepted by both
3. NO_TOTAL — missing all financial amounts → rejected by both (upload: FAILED job; camera: 422)
4. UNCERTAIN — low-confidence vendor/date/currency, but has TOTAL label → accepted + flagged review
5. JUNK — garbage text → rejected by both (same as NO_TOTAL)
6. DUPLICATE_SAME_FILE — same content uploaded twice → merged (1 row) on both
7. DUPLICATE_NUMBER_DIFF_FILE — same invoice_number, different file → rejected (upload: FAILED job; camera: 409)
8. UNSUPPORTED_TYPE — file type not in allowed list → 415 before extraction on both

All tests exercise real production paths:
- /upload: full sync extraction (WORKER_ENABLED=0 default) → repo upsert → _upload_result
- /camera-upload: OCR boundary patched to return fixture text → extract_invoice → shared rule → repo upsert
"""

import pytest
from fastapi.testclient import TestClient
from io import BytesIO
from PIL import Image, PngImagePlugin

# ─── Fixtures (text that the extractor actually reads) ───

# 1) Retail receipt: VN small business, no invoice number, has total label
RETAIL = """HÓA ĐƠN BÁN LẺ
Người bán: CÔNG TY TNHH SIÊU THỊ AN PHÚ
Ngày 05/11/2026
Tổng cộng tiền thanh toán: 125.000
"""

# 2) GTGT (from test_api.py) — formal invoice with number + total
GTGT = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00012345
Người bán: CÔNG TY TNHH ABC
Cộng tiền hàng hóa, dịch vụ: 29,000,000
Chiết khấu thương mại: 1,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 30,900,000
Ngày 04/08/2026
"""

# 3) No financial amount at all — just vendor/date/thanks
NO_TOTAL = """HÓA ĐƠN BÁN HÀNG
Người bán: CÔNG TY TNHH ABC
Ngày 05/11/2026
Cảm ơn quý khách!
"""

# 4) Uncertain OCR: vendor/date/currency low conf, but TOTAL keyword present
UNCERTAIN = """SIÊU THỊ AN PHÚ
05/11/2026
TOTAL 125.000
"""

# 5) Junk text — no structure, no amounts
JUNK = """not an invoice at all"""

# 6/7) Duplicate numbers use the GTGT number
DUP_NUMBER = "00012345"

# 8) Unsupported MIME for /upload (defaults include text/plain, image/*, application/pdf, xml)
#    For camera: allowed_mimes = ["image/png"], so application/x-msdownload is disallowed


# ─── Helpers ───

def _register(client: TestClient, username: str, password: str = "matkhau123") -> str:
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    from src import app as app_module
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_png_bytes(image_tag: str = "") -> bytes:
    buf = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    if image_tag:
        metadata.add_text("test_tag", image_tag)
    Image.new("RGB", (200, 200), "white").save(buf, format="PNG", pnginfo=metadata)
    return buf.getvalue()


# ─── /upload tests ───

@pytest.fixture
def upload_client(monkeypatch, tmp_path):
    """Mirror of tests/test_api.py: fresh DB, OPEN_REGISTRATION, worker disabled."""
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("WORKER_ENABLED", "0")  # synchronous extraction in tests
    from src import app as app_module
    from src.config import Settings
    from src.store.sql_repository import Database, InvoiceRepository, TransactionRepository, UserRepository
    monkeypatch.setattr(app_module, "get_settings", lambda: Settings())
    app_db = Database(str(tmp_path / "test.db"))
    app_db.create_tables()
    app_module.repo = InvoiceRepository(app_db)
    app_module.users = UserRepository(app_db)
    app_module.tx_repo = TransactionRepository(app_db)
    from src.security.idempotency import InMemoryCache
    app_module._upload_idempotency = InMemoryCache()
    yield TestClient(app_module.app)


def test_retail_receipt_without_invoice_number_accepted_by_upload(upload_client):
    """RETAIL fixture: no invoice_number, has total → 202, job_status=review, invoice_number='unknown'."""
    token = _register(upload_client, "retail_user")
    data = RETAIL.encode("utf-8")
    r = upload_client.post(
        "/upload",
        files={"file": ("receipt.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "review"
    assert inv["payment_match_status"] == "unmatched"
    assert inv["invoice_number"] == "unknown"
    assert inv["vendor"] == "CÔNG TY TNHH SIÊU THỊ AN PHÚ"
    assert inv["total"] == 125000.0
    assert inv["currency"] == "VND"
    # Provenance recorded (may be empty dict if extractor skipped)
    assert "provenance" in inv


def test_gtgt_number_and_total_preserved_on_upload(upload_client):
    """GTGT fixture: formal invoice with number + total → all fields extracted."""
    token = _register(upload_client, "gtgt_user")
    data = GTGT.encode("utf-8")
    r = upload_client.post(
        "/upload",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "review"
    assert inv["invoice_number"] == "00012345"
    assert inv["vendor"] == "CÔNG TY TNHH ABC"
    assert inv["total"] == 30900000.0
    assert inv["currency"] == "VND"


def test_missing_total_fails_upload(upload_client):
    """NO_TOTAL fixture: no financial amount → extraction fails, job_status=failed, no payment match."""
    token = _register(upload_client, "nototal_user")
    data = NO_TOTAL.encode("utf-8")
    r = upload_client.post(
        "/upload",
        files={"file": ("receipt.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "failed"
    assert inv["payment_match_status"] == "failed"
    assert inv["matched_transaction_id"] is None
    # Invoice row exists (created before extraction) but with minimal data
    assert inv["invoice_number"] == "unknown"
    assert inv["vendor"] == "unknown"
    assert inv["total"] == 0.0


def test_uncertain_ocr_flagged_for_review_on_upload(upload_client):
    """UNCERTAIN fixture: TOTAL present but low confidence on vendor/date/currency → job_status=review, needs_review logic downstream."""
    token = _register(upload_client, "uncertain_user")
    data = UNCERTAIN.encode("utf-8")
    r = upload_client.post(
        "/upload",
        files={"file": ("receipt.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "review"
    assert inv["total"] == 125000.0
    assert inv["invoice_number"] == "unknown"
    # vendor may be "SIÊU THỊ AN PHÚ" with low provenance confidence
    assert any((prov["confidence"] or 0) < 0.7 for prov in inv["provenance"].values())


def test_junk_text_rejected_on_upload(upload_client):
    """JUNK fixture: no financial amount → job_status=failed like NO_TOTAL."""
    token = _register(upload_client, "junk_user")
    data = JUNK.encode("utf-8")
    r = upload_client.post(
        "/upload",
        files={"file": ("junk.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "failed"
    assert inv["payment_match_status"] == "failed"


def test_duplicate_same_file_merged_on_upload(upload_client):
    """Same file content uploaded twice (different filenames) → one invoice row, same id."""
    token = _register(upload_client, "dup_user")
    data = GTGT.encode("utf-8")
    r1 = upload_client.post("/upload", files={"file": ("a.txt", data, "text/plain")}, headers=_auth(token))
    r2 = upload_client.post("/upload", files={"file": ("b.txt", data, "text/plain")}, headers=_auth(token))
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    # Only 1 invoice in DB
    all_inv = upload_client.get("/invoices", headers=_auth(token)).json()
    assert len(all_inv) == 1


def test_duplicate_number_different_file_fails_upload(upload_client):
    """Same invoice_number, different file content → duplicate detected, job_status=failed (async path: 202 then FAILED)."""
    token = _register(upload_client, "dup2_user")
    # First upload — GTGT with number 00012345
    r1 = upload_client.post("/upload", files={"file": ("orig.txt", GTGT.encode("utf-8"), "text/plain")}, headers=_auth(token))
    assert r1.status_code == 202
    id1 = r1.json()["id"]
    # Second upload — different text, same invoice number
    dup_text = f"""HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: {DUP_NUMBER}
Người bán: CÔNG TY KHÁC
Tổng cộng tiền thanh toán: 50.000
"""
    r2 = upload_client.post("/upload", files={"file": ("dup.txt", dup_text.encode("utf-8"), "text/plain")}, headers=_auth(token))
    assert r2.status_code == 202
    inv2 = r2.json()
    # In sync mode (WORKER_ENABLED=0), extraction runs inline and DuplicateInvoiceError is caught
    # → row marked FAILED. Id may differ from id1 (created before extraction) but no 2nd row kept.
    assert inv2["job_status"] == "failed"
    assert inv2["payment_match_status"] == "failed"
    # Keep the failed upload attempt as a visible row, but do not persist the
    # duplicate extracted number or financial details on that placeholder.
    all_inv = upload_client.get("/invoices", headers=_auth(token)).json()
    assert len(all_inv) == 2
    assert {inv["id"] for inv in all_inv} == {id1, inv2["id"]}
    rejected = next(inv for inv in all_inv if inv["id"] == inv2["id"])
    assert rejected["invoice_number"] == "unknown"
    # Preserve extracted data/provenance for review even though the duplicate
    # number itself is not stored as a second invoice identity.
    assert rejected["total"] == 50000.0
    assert rejected["vendor"] == "CÔNG TY KHÁC"
    assert rejected["provenance"]["invoice_number"]["value"] == DUP_NUMBER


def test_unsupported_type_rejected_before_extraction_on_upload(upload_client, monkeypatch):
    """A valid text file is rejected when this endpoint is configured to allow only PDF."""
    from dataclasses import replace
    from src import app as app_module

    token = _register(upload_client, "badtype_user")
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, allowed_mime_types=["application/pdf"])
    )
    r = upload_client.post(
        "/upload",
        files={"file": ("receipt.txt", RETAIL.encode("utf-8"), "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 415, r.text
    assert r.json()["error"]["code"] == "DISALLOWED_FILE_TYPE"
    assert app_module.repo.list(user_id=app_module.users.get_by_username("badtype_user").id) == []


# ─── /camera-upload tests ───
# Patches: payments.get_settings (storage dir, max bytes, allowed_mimes=["image/png"])
#          payments._get_db (tmp sqlite)
#          payments.current_user dependency override
#          extractor._extract_text_with_layout (OCR boundary) → return fixture text

@pytest.fixture
def camera_client(monkeypatch, tmp_path):
    """Mirror of tests/test_payments_api.py:payments_client but with monkeypatch for OCR boundary."""
    from src import app as app_module
    from src.routers import payments
    from src.store.sql_repository import Database
    from src.models import User
    from types import SimpleNamespace

    db = Database(str(tmp_path / "camera.db"))
    db.create_tables()
    with db.get_session() as session:
        user = User(id="owner", username="owner", password_hash="x" * 60, verified=True)
        session.add(user)
        session.commit()

    monkeypatch.setattr(payments, "_get_db", lambda: db)
    monkeypatch.setattr(
        payments,
        "get_settings",
        lambda: SimpleNamespace(
            storage_dir=str(tmp_path / "camera-storage"),
            max_upload_bytes=2_000_000,
            allowed_mime_types=["image/png"],
            max_pdf_pages=10,
        ),
    )
    app_module.app.dependency_overrides[payments.current_user] = lambda: User(
        id="owner", username="owner", password_hash="x" * 60, verified=True
    )

    # OCR boundary: will be overridden per-test
    from src.extract import extractor
    monkeypatch.setattr(
        extractor,
        "_extract_text_with_layout",
        lambda content, mime_type: (JUNK, None),  # default fallback
    )

    yield TestClient(app_module.app), db

    app_module.app.dependency_overrides.clear()


def _camera_post(
    client: TestClient,
    fixture_text: str,
    mime: str = "image/png",
    image_tag: str = "",
):
    """Post to /payments/camera-upload with a real PNG and patched OCR returning fixture_text."""
    from src.extract import extractor
    extractor._extract_text_with_layout = lambda content, content_type: (fixture_text, None)
    png = _make_png_bytes(image_tag)
    response = client.post(
        "/payments/camera-upload",
        files={"file": ("receipt.png", png, mime)},
    )
    return response


def test_retail_receipt_without_invoice_number_accepted_by_camera(camera_client, monkeypatch, tmp_path):
    """RETAIL: camera accepts no invoice_number, needs_review=False (all prov >= 0.8)."""
    client, db = camera_client
    r = _camera_post(client, RETAIL)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["needs_review"] is False
    assert data["message"] == "Đã lưu hóa đơn để rà soát."
    # Invoice row in DB
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    invs = repo.list(user_id="owner")
    assert len(invs) == 1
    inv = invs[0]
    assert inv.invoice_number == "unknown"
    assert inv.vendor == "CÔNG TY TNHH SIÊU THỊ AN PHÚ"
    assert inv.total == 125000.0
    assert inv.currency == "VND"
    # Storage file exists
    assert len(list((tmp_path / "camera.db").parent.glob("**/*.png"))) > 0


def test_gtgt_number_and_total_preserved_on_camera(camera_client, monkeypatch, tmp_path):
    """GTGT on camera → invoice with number + total saved."""
    client, db = camera_client
    r = _camera_post(client, GTGT)
    assert r.status_code == 200, r.text
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    invs = repo.list(user_id="owner")
    assert len(invs) == 1
    inv = invs[0]
    assert inv.invoice_number == "00012345"
    assert inv.vendor == "CÔNG TY TNHH ABC"
    assert inv.total == 30900000.0
    assert inv.currency == "VND"


def test_missing_total_rejected_by_camera(camera_client, monkeypatch, tmp_path):
    """NO_TOTAL: camera returns 422 EXTRACTION_FAILED, zero invoice rows, empty storage."""
    client, db = camera_client
    r = _camera_post(client, NO_TOTAL)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "EXTRACTION_FAILED"
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    invs = repo.list(user_id="owner")
    assert len(invs) == 0
    # Camera deletes its storage file on error
    assert not list(tmp_path.glob("**/*.png"))


def test_uncertain_ocr_flagged_needs_review_on_camera(camera_client, monkeypatch, tmp_path):
    """UNCERTAIN: vendor/date/currency low confidence → needs_review=True, invoice saved with total."""
    client, db = camera_client
    r = _camera_post(client, UNCERTAIN)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["needs_review"] is True
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    invs = repo.list(user_id="owner")
    assert len(invs) == 1
    inv = invs[0]
    assert inv.invoice_number == "unknown"
    assert inv.total == 125000.0
    assert inv.currency == "USD"  # inferred from "TOTAL" label (no VND marker)
    assert any((prov.get("confidence") or 0) < 0.7 for prov in inv.provenance.values())


def test_junk_text_rejected_by_camera(camera_client, monkeypatch, tmp_path):
    """JUNK: same as NO_TOTAL → 422, zero rows, empty storage."""
    client, db = camera_client
    r = _camera_post(client, JUNK)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "EXTRACTION_FAILED"
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    invs = repo.list(user_id="owner")
    assert len(invs) == 0


def test_duplicate_same_file_merged_on_camera(camera_client, monkeypatch, tmp_path):
    """Same file content is reused without leaving an unreferenced retry image."""
    client, db = camera_client
    first = _camera_post(client, GTGT)
    assert first.status_code == 200, first.text
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    original = repo.get(first.json()["invoice_id"], user_id="owner")
    assert original is not None
    storage_dir = tmp_path / "camera-storage"
    assert (storage_dir / original.source_file).exists()

    second = _camera_post(client, GTGT)

    assert second.status_code == 200, second.text
    assert second.json()["invoice_id"] == original.id
    assert repo.list(user_id="owner")[0].source_file == original.source_file
    stored_files = list(storage_dir.iterdir())
    assert stored_files == [storage_dir / original.source_file]


def test_duplicate_number_different_file_rejected_409_on_camera(camera_client, monkeypatch, tmp_path):
    """Same invoice_number, different file → 409 DUPLICATE_INVOICE, camera deletes its storage copy, DB unchanged."""
    client, db = camera_client
    r1 = _camera_post(client, GTGT)
    assert r1.status_code == 200
    id1 = r1.json()["invoice_id"]
    # Second upload with same number, different text
    dup_text = f"""HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: {DUP_NUMBER}
Người bán: CÔNG TY KHÁC
Tổng cộng tiền thanh toán: 50.000
"""
    r2 = _camera_post(client, dup_text, image_tag="different-image")
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "DUPLICATE_INVOICE"
    assert r2.json()["error"]["details"]["existing_id"] == id1
    from src.store.sql_repository import InvoiceRepository
    repo = InvoiceRepository(db)
    invs = repo.list(user_id="owner")
    assert len(invs) == 1
    storage_dir = tmp_path / "camera-storage"
    assert (storage_dir / invs[0].source_file).exists()
    assert list(storage_dir.iterdir()) == [storage_dir / invs[0].source_file]


def test_unsupported_type_rejected_before_extraction_on_camera(camera_client):
    """Camera allows only image/png; a text/plain upload is rejected as MIME_MISMATCH."""
    client, db = camera_client
    text = RETAIL.encode("utf-8")
    r = client.post(
        "/payments/camera-upload",
        files={"file": ("receipt.txt", text, "text/plain")},
    )
    assert r.status_code == 415, r.text
    assert r.json()["error"]["code"] == "DISALLOWED_FILE_TYPE"
    from src.store.sql_repository import InvoiceRepository
    assert InvoiceRepository(db).list(user_id="owner") == []
