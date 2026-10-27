"""API integration tests — luồng thật: register → login → upload → report.
Dùng :memory: cho repo/users, không đụng DB file."""
import os
import pytest
from fastapi.testclient import TestClient
from src import app as app_module
from src.store.sql_repository import Database, InvoiceRepository, TransactionRepository, UserRepository
from src.models import User

@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    """Mỗi test dùng DB tạm mới. Đăng ký mở cho test."""
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    app_db = Database(str(tmp_path / "test.db"))
    app_db.create_tables()
    app_module.repo = InvoiceRepository(app_db)
    app_module.users = UserRepository(app_db)
    app_module.tx_repo = TransactionRepository(app_db)
    from src.security.idempotency import InMemoryCache
    app_module._upload_idempotency = InMemoryCache()
    yield


@pytest.fixture()
def transaction_db(tmp_path):
    # Use the same database as app_module.repo to avoid cross-database FK issues
    db = app_module.tx_repo.db
    db.create_tables()
    yield db


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username="kien", password="matkhau123") -> str:
    """Đăng ký + xác minh email + trả token."""
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


GTGT = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: 00012345
Người bán: CÔNG TY TNHH ABC
Cộng tiền hàng hóa, dịch vụ: 29,000,000
Chiết khấu thương mại: 1,000,000
Thuế GTGT: 2,900,000
Tổng cộng tiền thanh toán: 30,900,000
Ngày 04/08/2026
"""


# ---------- Auth ----------

def test_register_login_flow(client):
    """Đăng ký → đăng nhập → me."""
    token = _register(client)
    app_module.users.set_verified(app_module.users.get_by_username("kien").id, True)
    r = client.post("/auth/login", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 200
    assert "access_token" in r.json()

    r = client.get("/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["username"] == "kien"
    assert "password_hash" not in r.json()  # không lộ hash


def test_duplicate_register_rejected(client):
    """Username trùng → 409."""
    _register(client)
    r = client.post("/auth/register", json={"username": "kien", "password": "matkhau123"})
    assert r.status_code == 409


def test_wrong_password_rejected(client):
    """Sai mật khẩu → 401."""
    _register(client)
    r = client.post("/auth/login", json={"username": "kien", "password": "wrongpassword"})
    assert r.status_code == 401


def test_invoices_require_auth(client):
    """API hóa đơn yêu cầu auth → 401."""
    r = client.get("/invoices")
    assert r.status_code == 401


def test_invalid_token_rejected(client):
    """Token sai → 401."""
    r = client.get("/invoices", headers=_auth("token-sai"))
    assert r.status_code == 401


# ---------- Upload & Extraction ----------

def test_upload_gtgt_full_flow(client):
    """Upload GTGT → extraction chạy xong (fallback đồng bộ) → trả trạng thái thật."""
    token = _register(client)
    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "review"
    assert inv["payment_match_status"] == "unmatched"
    assert inv["matched_transaction_id"] is None
    assert inv["invoice_number"] == "00012345"
    assert inv["vendor"] == "CÔNG TY TNHH ABC"
    assert inv["total"] == 30900000.0


def test_upload_async_enqueued_returns_processing(client, monkeypatch):
    """WORKER_ENABLED=1 → 202 "processing" ngay, invoice chờ worker xử lý."""
    from src.workers import extraction_worker

    token = _register(client)
    monkeypatch.setenv("WORKER_ENABLED", "1")
    enqueued = []

    class FakeTask:
        def delay(self, *args):
            enqueued.append(args)

    monkeypatch.setattr(extraction_worker, "extract_invoice", FakeTask())

    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "processing"
    assert inv["payment_match_status"] == "processing"
    assert inv["matched_transaction_id"] is None
    assert len(enqueued) == 1 and enqueued[0][0] == inv["id"]

    # Worker xử lý xong (mô phỏng) → invoice chuyển sang review
    extraction_worker._do_extract(
        inv["id"], inv["source_file"], inv["user_id"],
        invoice_repo=app_module.repo, tx_repo=app_module.tx_repo,
    )
    fresh = app_module.repo.get(inv["id"], user_id=inv["user_id"])
    assert fresh.job_status.value == "review"
    assert fresh.invoice_number == "00012345"


def test_enqueue_failure_falls_back_to_sync_extraction(client, monkeypatch):
    """Broker chết khi WORKER_ENABLED=1 → fallback chạy đồng bộ, vẫn trả kết quả."""
    from src.workers import extraction_worker

    token = _register(client)
    monkeypatch.setenv("WORKER_ENABLED", "1")

    class DeadBroker:
        def delay(self, *args):
            raise RuntimeError("broker down")

    monkeypatch.setattr(extraction_worker, "extract_invoice", DeadBroker())

    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "review"
    assert inv["invoice_number"] == "00012345"


def test_worker_retry_keeps_processing_until_retries_exhausted(client):
    """Celery path: mark_failed_on_error=False → lỗi giữ PROCESSING (retry chạy
    lại được, không bị skip); FAILED chỉ đặt khi hết lượt (_set_job_status)."""
    from src.domain.models import JobStatus
    from src.models import Invoice
    from src.workers.extraction_worker import _do_extract, _set_job_status

    _register(client, "retryu", "pass123456")
    owner_id = app_module.users.get_by_username("retryu").id
    inv = app_module.repo.upsert(
        Invoice(user_id=owner_id, source_file="missing.txt", job_status=JobStatus.PROCESSING)
    )
    with pytest.raises(Exception):
        _do_extract(
            inv.id, "missing-storage-key.txt", owner_id,
            invoice_repo=app_module.repo, tx_repo=app_module.tx_repo,
            mark_failed_on_error=False,
        )
    assert app_module.repo.get(inv.id, user_id=owner_id).job_status.value == "processing"

    _set_job_status(inv.id, JobStatus.FAILED)
    assert app_module.repo.get(inv.id, user_id=owner_id).job_status.value == "failed"


def test_unprocessable_file_marks_failed(client):
    """File rác → extraction lỗi → 202 với job_status=failed."""
    token = _register(client)
    r = client.post(
        "/upload",
        files={"file": ("junk.txt", b"not an invoice at all", "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "failed"
    assert inv["payment_match_status"] == "failed"


# ---------- Invoice CRUD ----------

def test_upload_for_selected_transaction_matches_exact_idempotently(client, transaction_db):
    """Upload kèm transaction_id khớp số tiền → link đúng giao dịch đã chọn."""
    token = _register(client)
    owner_id = app_module.users.get_by_username("kien").id
    tx = app_module.tx_repo.create(__import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        id="selected-payment", user_id=owner_id, amount=30900000, currency="VND",
        status=__import__("src.domain.payments", fromlist=["TxStatus"]).TxStatus.PENDING_RECEIPT,
    ))
    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload?transaction_id=selected-payment",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "finalized"
    assert inv["payment_match_status"] == "matched"
    assert inv["matched_transaction_id"] == "selected-payment"
    assert app_module.tx_repo.get(tx.id, owner_id).matched_invoice_id == inv["id"]
    assert app_module.tx_repo.get(tx.id, owner_id).status.value == "matched"
    assert app_module.repo.get(inv["id"], user_id=owner_id).status.value == "paid"


def test_selected_transaction_invoice_amount_mismatch_is_not_linked(client, transaction_db):
    """Upload kèm transaction_id lệch số tiền → không link, giao dịch vẫn chờ."""
    token = _register(client)
    owner_id = app_module.users.get_by_username("kien").id
    tx = app_module.tx_repo.create(__import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        id="mismatch-payment", user_id=owner_id, amount=100, currency="VND",
    ))
    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload?transaction_id=mismatch-payment",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["job_status"] == "review"
    assert inv["payment_match_status"] == "amount_mismatch"
    assert inv["matched_transaction_id"] is None
    assert app_module.tx_repo.get(tx.id, owner_id).matched_invoice_id is None
    assert app_module.repo.get(inv["id"], user_id=owner_id).status.value == "unpaid"


def test_selected_transaction_links_even_when_amount_ambiguous(client, transaction_db):
    """2 giao dịch cùng số tiền (auto-match sẽ ambiguous) → chọn tường minh vẫn
    link đúng giao dịch user đã chọn, giao dịch còn lại giữ nguyên."""
    token = _register(client)
    owner_id = app_module.users.get_by_username("kien").id
    tx_a = app_module.tx_repo.create(__import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        id="tx-a", user_id=owner_id, amount=30900000, currency="VND",
        status=__import__("src.domain.payments", fromlist=["TxStatus"]).TxStatus.PENDING_RECEIPT,
    ))
    tx_b = app_module.tx_repo.create(__import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        id="tx-b", user_id=owner_id, amount=30900000, currency="VND",
        status=__import__("src.domain.payments", fromlist=["TxStatus"]).TxStatus.PENDING_RECEIPT,
    ))
    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload?transaction_id=tx-a",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["payment_match_status"] == "matched"
    assert inv["matched_transaction_id"] == "tx-a"
    assert app_module.tx_repo.get(tx_a.id, owner_id).status.value == "matched"
    assert app_module.tx_repo.get(tx_b.id, owner_id).status.value == "pending_receipt"


def test_selected_transaction_upload_rejects_foreign_owner(client, transaction_db):
    """Upload chọn transaction của user khác → từ chối ngay (không tạo invoice)."""
    token = _register(client, "alice", "pass123456")
    other_token = _register(client, "bob", "pass123456")
    owner_id = app_module.users.get_by_username("alice").id
    app_module.tx_repo.create(__import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
        id="foreign-payment", user_id=owner_id, amount=10,
    ))
    data = GTGT.encode("utf-8")
    r = client.post(
        "/upload?transaction_id=foreign-payment",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=_auth(other_token),
    )
    assert r.status_code == 409, r.text


def test_edit_invoice_retries_matching_against_pending_transaction(client, transaction_db):
    token = _register(client)
    user_id = app_module.users.get_by_username("kien").id
    app_module.repo = __import__("src.store.sql_repository", fromlist=["InvoiceRepository"]).InvoiceRepository(transaction_db)
    app_module.tx_repo = TransactionRepository(transaction_db)
    transaction = app_module.tx_repo.create(
        __import__("src.models", fromlist=["PaymentTransaction"]).PaymentTransaction(
            id="edit-match-payment", user_id=user_id, amount=500000, currency="VND",
        )
    )
    invoice = app_module.repo.create(
        __import__("src.models", fromlist=["InvoiceCreate"]).InvoiceCreate(
            invoice_number="EDIT-MATCH", vendor="Shop", total=50000, currency="VND",
        ), user_id=user_id,
    )

    response = client.put(
        f"/invoices/{invoice.id}", json={"total": 500000}, headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["payment_match_status"] == "matched"
    assert response.json()["matched_transaction_id"] == transaction.id
    assert app_module.tx_repo.get(transaction.id, user_id).status.value == "matched"
    assert app_module.repo.get(invoice.id, user_id=user_id).status.value == "paid"
    app_module.repo = InvoiceRepository(app_module.tx_repo.db)
    app_module.tx_repo = TransactionRepository(Database(":memory:"))
    app_module.tx_repo.db.create_tables()


def test_create_list_paid_flow(client):
    """Tạo hóa đơn → list → cập nhật paid."""
    token = _register(client)
    # Tạo hóa đơn
    r = client.post(
        "/invoices",
        json={"invoice_number": "INV-001", "vendor": "Test", "total": 100.0},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]

    # List
    r = client.get("/invoices", headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Update to paid
    r = client.put(
        f"/invoices/{inv_id}",
        json={"status": "paid"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "paid"


def test_multi_user_isolation_api(client):
    """User 2 không thấy invoice của user 1."""
    token1 = _register(client, "user1", "pass123456")
    token2 = _register(client, "user2", "pass123456")

    # User 1 tạo invoice
    r = client.post(
        "/invoices",
        json={"invoice_number": "INV-001", "vendor": "Test", "total": 100.0},
        headers=_auth(token1),
    )
    assert r.status_code == 200
    inv_id = r.json()["id"]

    # User 2 không thấy
    r = client.get(f"/invoices/{inv_id}", headers=_auth(token2))
    assert r.status_code == 404


def test_report_empty_month(client):
    """Báo cáo tháng trống → 404."""
    token = _register(client)
    r = client.get("/reports/monthly/2024-01", headers=_auth(token))
    assert r.status_code == 404


# ---------- Worker push events (invoice.review / invoice.matched) ----------

VAPID_PUB = "BNVBzrWUcCA7GOOCUOgBWB27-LGwKMXezWFV3qEEayho18hxANT5fLFRvIGkPVh0fLezbcelsw5UYRadsQKGV1Q"
PUSH_SUB = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/worker-test",
    "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQt", "auth": "tBHItJI5svbpez7KI4CCXg"},
}
# Bản GTGT số khác để tránh DUPLICATE_INVOICE khi upload lần 2
GTGT_2 = GTGT.replace("00012345", "00012346")


def test_worker_pushes_review_after_extraction(client, monkeypatch):
    """Extraction xong chưa ghép được → push nhắc vào review."""
    import json

    from src import push as push_module
    from src.store.sql_repository import PushSubscriptionRepository

    monkeypatch.setenv("VAPID_PUBLIC_KEY", VAPID_PUB)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "./vapid_private.pem")
    calls = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw))

    token = _register(client)
    user_id = app_module.users.get_by_username("kien").id
    # push repo cùng DB fixture (app_module.push_repo trỏ DB import-time khác)
    app_module.push_repo = PushSubscriptionRepository(app_module.users.db)
    app_module.push_repo.save(user_id, PUSH_SUB)

    r = client.post(
        "/upload",
        files={"file": ("hoadon.txt", GTGT.encode("utf-8"), "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    assert len(calls) == 1
    payload = json.loads(calls[0]["data"])
    assert "00012345" in payload["title"]
    assert "kiểm tra" in payload["title"]


def test_worker_pushes_matched_after_auto_link(client, monkeypatch):
    """Upload kèm giao dịch khớp → push báo đã ghép tự động."""
    import json

    from src import push as push_module
    from src.domain.payments import TxStatus
    from src.models import PaymentTransaction
    from src.store.sql_repository import PushSubscriptionRepository

    monkeypatch.setenv("VAPID_PUBLIC_KEY", VAPID_PUB)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "./vapid_private.pem")
    calls = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw))

    token = _register(client)
    user_id = app_module.users.get_by_username("kien").id
    app_module.push_repo = PushSubscriptionRepository(app_module.users.db)
    app_module.push_repo.save(user_id, PUSH_SUB)
    app_module.tx_repo.create(PaymentTransaction(
        id="push-match-tx", user_id=user_id, amount=30900000, currency="VND",
        status=TxStatus.PENDING_RECEIPT,
    ))

    r = client.post(
        "/upload?transaction_id=push-match-tx",
        files={"file": ("hoadon2.txt", GTGT_2.encode("utf-8"), "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    assert r.json()["job_status"] == "finalized"
    assert len(calls) == 1
    payload = json.loads(calls[0]["data"])
    assert "đã được ghép tự động" in payload["title"]
