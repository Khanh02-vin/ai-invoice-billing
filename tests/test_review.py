"""Review UI: sửa field máy đọc sai → provenance manual + review_history + export eval."""

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.store.repository import InvoiceRepository
from src.store.users import UserRepository


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    app_module.repo = InvoiceRepository(db_path=":memory:")
    app_module.users = UserRepository(db_path=":memory:")
    from src.security.idempotency import InMemoryCache
    app_module._upload_idempotency = InMemoryCache()
    yield


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username="reviewer"):
    r = client.post("/auth/register", json={"username": username, "password": "matkhau123"})
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# Hóa đơn mà extractor đọc SAI số tiền (total_ok=2,000,000 thay vì 20,000,000)
SAMPLE = """HÓA ĐƠN BÁN HÀNG
Số hóa đơn: HD-REV-1
Người bán: CÔNG TY XYZ
Ngày lập: 04/08/2026
Tổng cộng tiền thanh toán: 2,000,000
"""

SAMPLE_TOTAL_OK = """HÓA ĐƠN BÁN HÀNG
Số hóa đơn: HD-REV-2
Người bán: CÔNG TY XYZ
Ngày lập: 05/08/2026
Tổng cộng tiền thanh toán: 20,000,000
"""


def _upload(client, h, name, text):
    r = client.post("/upload", files={"file": (name, text.encode(), "text/plain")}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_fills_provenance(client):
    """Upload → response có provenance từng field (confidence + source) cho UI review."""
    h = _register(client)
    inv = _upload(client, h, "rev1.txt", SAMPLE)
    prov = inv["provenance"]
    assert prov["total"]["source"] == "regex"
    assert 0 < prov["total"]["confidence"] <= 1
    assert prov["vendor"]["confidence"] == 0.85      # nhãn "Người bán:" rõ ràng
    assert set(prov) >= {"invoice_number", "vendor", "issue_date", "total"}


def test_patch_fields_logs_correction(client):
    """User sửa total → review_history ghi old/new + provenance cũ; provenance mới = manual."""
    h = _register(client)
    inv = _upload(client, h, "rev1.txt", SAMPLE)
    assert inv["total"] == 2000000.0

    r = client.put(f"/invoices/{inv['id']}", json={"total": 20000000.0}, headers=h)
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["total"] == 20000000.0

    hist = updated["review_history"]
    assert len(hist) == 1
    c = hist[0]
    assert c["field"] == "total"
    assert c["old_value"] == 2000000.0
    assert c["new_value"] == 20000000.0
    assert c["source"] == "regex"          # provenance lúc máy đọc
    assert c["confidence"] is not None
    assert c["corrected_by"]                  # có user id
    assert updated["provenance"]["total"]["source"] == "manual"
    assert updated["provenance"]["total"]["confidence"] == 1.0

    # GET lại từ DB — correction đã persist
    got = client.get(f"/invoices/{inv['id']}", headers=h).json()
    assert got["total"] == 20000000.0
    assert len(got["review_history"]) == 1


def test_patch_unchanged_value_not_logged(client):
    """Gửi lại giá trị cũ (không đổi) → không sinh correction rác cho eval."""
    h = _register(client)
    inv = _upload(client, h, "rev2.txt", SAMPLE_TOTAL_OK)
    r = client.put(f"/invoices/{inv['id']}", json={"total": 20000000.0}, headers=h)
    assert r.status_code == 200
    assert r.json()["review_history"] == []


def test_export_corrections_eval_data(client):
    """GET /eval/corrections → data eval: field, old (máy đọc), new (user sửa), confidence."""
    h = _register(client)
    inv = _upload(client, h, "rev1.txt", SAMPLE)
    client.put(f"/invoices/{inv['id']}", json={"total": 20000000.0, "vendor": "CÔNG TY XYZ (đúng)"}, headers=h)

    r = client.get("/eval/corrections", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2
    by_field = {c["field"]: c for c in data}
    assert by_field["total"]["old_value"] == 2000000.0
    assert by_field["total"]["new_value"] == 20000000.0
    assert by_field["vendor"]["source"] == "regex"
    assert data[0]["invoice_id"] == inv["id"]


def test_corrections_isolated_between_users(client):
    """Corrections của user A không lộ sang user B."""
    ha = _register(client, "alice")
    inv = _upload(client, ha, "a.txt", SAMPLE)
    client.put(f"/invoices/{inv['id']}", json={"total": 1.0}, headers=ha)

    hb = _register(client, "bob")
    assert client.get(f"/invoices/{inv['id']}", headers=hb).status_code == 404
    assert client.get("/eval/corrections", headers=hb).json() == []


def test_corrected_invoice_via_worker_path(client):
    """Sửa qua repo.update (không qua API) cũng ghi correction — 1 đường duy nhất."""
    h = _register(client)
    inv = _upload(client, h, "rev1.txt", SAMPLE)
    from src.domain.models import InvoiceUpdate
    repo = app_module.repo
    uid = app_module.users.get_by_username("reviewer").id
    updated = repo.update(inv["id"], InvoiceUpdate(issue_date="2026-08-07"), user_id=uid)
    assert updated.issue_date == "2026-08-07"
    assert updated.provenance["issue_date"].source == "manual"
    assert len(updated.review_history) == 1
    assert updated.review_history[0]["field"] == "issue_date"
