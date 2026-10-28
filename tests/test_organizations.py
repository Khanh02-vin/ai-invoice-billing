"""Multi-tenant organization — quản lý org + ngữ cảnh X-Organization-Id.

Semantics quan trọng (docs/multi-tenant-spec.md mục 3): org context lọc theo
organization_id (thấy hóa đơn của đồng nghiệp), cá nhân lọc theo user_id —
KHÔNG AND hai điều kiện.
"""
import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.store.sql_repository import (
    ApiKeyRepository,
    Database,
    InvoiceRepository,
    OrganizationRepository,
    TransactionRepository,
    UserRepository,
)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    # 1 test đăng ký tới 4 user (owner + member + viewer + outsider) — nới limit
    monkeypatch.setenv("RL_AUTH_REGISTER", "100:60")
    monkeypatch.setenv("RL_AUTH_LOGIN", "100:60")
    app_db = Database(str(tmp_path / "test.db"))
    app_db.create_tables()
    app_module.repo = InvoiceRepository(app_db)
    app_module.users = UserRepository(app_db)
    app_module.tx_repo = TransactionRepository(app_db)
    app_module.api_keys_repo = ApiKeyRepository(app_db)
    app_module.orgs_repo = OrganizationRepository(app_db)
    try:
        from src.security.rate_limit import get_default_limiter

        get_default_limiter().clear()
    except Exception:
        pass
    yield


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username, password="matkhau123") -> str:
    r = client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _setup_org(client, name="Công ty A") -> tuple[str, dict]:
    """ownera tạo org, mời memberb (member) + viewerv (viewer) → (org_id, tokens)."""
    token_a = _register(client, "ownera")
    r = client.post("/organizations", json={"name": name}, headers=_auth(token_a))
    assert r.status_code == 200, r.text
    org_id = r.json()["id"]
    assert r.json()["role"] == "owner"
    token_b = _register(client, "memberb")
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "memberb", "role": "member"},
        headers=_auth(token_a),
    )
    assert r.status_code == 200, r.text
    token_v = _register(client, "viewerv")
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "viewerv", "role": "viewer"},
        headers=_auth(token_a),
    )
    assert r.status_code == 200, r.text
    return org_id, {"a": token_a, "b": token_b, "v": token_v}


# ---------- Quản lý org ----------

def test_create_and_list_my_organizations(client):
    token_a = _register(client, "ownera")
    r = client.post("/organizations", json={"name": "Công ty A"}, headers=_auth(token_a))
    assert r.status_code == 200, r.text
    org = r.json()
    assert org["name"] == "Công ty A" and org["role"] == "owner" and org["slug"]

    mine = client.get("/organizations", headers=_auth(token_a)).json()
    assert [o["id"] for o in mine] == [org["id"]]

    # User khác không thấy org này
    token_x = _register(client, "otherx")
    assert client.get("/organizations", headers=_auth(token_x)).json() == []


def test_member_management_rules(client):
    org_id, t = _setup_org(client)

    members = client.get(f"/organizations/{org_id}/members", headers=_auth(t["a"])).json()
    assert {m["username"]: m["role"] for m in members} == {
        "ownera": "owner", "memberb": "member", "viewerv": "viewer",
    }

    # member không quản lý được thành viên
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "ownera", "role": "member"},
        headers=_auth(t["b"]),
    )
    assert r.status_code == 403

    # viewer cũng không
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "ownera", "role": "member"},
        headers=_auth(t["v"]),
    )
    assert r.status_code == 403

    # viewer vẫn đọc được danh sách thành viên
    assert client.get(f"/organizations/{org_id}/members", headers=_auth(t["v"])).status_code == 200

    # đổi role, xóa thành viên: admin+; owner bất khả xâm phạm
    r = client.patch(
        f"/organizations/{org_id}/members/{_uid(client, 'memberb')}",
        json={"role": "viewer"}, headers=_auth(t["a"]),
    )
    assert r.status_code == 200 and r.json()["role"] == "viewer"

    r = client.delete(
        f"/organizations/{org_id}/members/{_uid(client, 'ownera')}", headers=_auth(t["a"])
    )
    assert r.status_code == 403
    r = client.patch(
        f"/organizations/{org_id}/members/{_uid(client, 'ownera')}",
        json={"role": "member"}, headers=_auth(t["a"]),
    )
    assert r.status_code == 403


def _uid(client, username) -> str:
    return app_module.users.get_by_username(username).id


def test_add_member_validation(client):
    org_id, t = _setup_org(client)
    # username không tồn tại
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "khongton tai", "role": "member"}, headers=_auth(t["a"]),
    )
    assert r.status_code == 404
    # role không hợp lệ khi thêm (owner chỉ qua tạo org)
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "ownera", "role": "owner"}, headers=_auth(t["a"]),
    )
    assert r.status_code == 400
    # trùng thành viên
    r = client.post(
        f"/organizations/{org_id}/members",
        json={"username": "memberb", "role": "member"}, headers=_auth(t["a"]),
    )
    assert r.status_code == 409


def test_non_member_forbidden_and_org_404(client):
    org_id, t = _setup_org(client)
    token_x = _register(client, "outsider")
    r = client.get("/invoices", headers={**_auth(token_x), "X-Organization-Id": org_id})
    assert r.status_code == 403
    r = client.get("/invoices", headers={**_auth(t["a"]), "X-Organization-Id": "khong-co"})
    assert r.status_code == 404


# ---------- Semantics dữ liệu org ----------

def test_member_sees_other_members_org_invoices(client):
    """Case thiết kế gốc làm sai: org context phải thấy hóa đơn của đồng nghiệp."""
    org_id, t = _setup_org(client)
    r = client.post(
        "/invoices",
        json={"invoice_number": "ORG-001", "vendor": "Vendor A", "total": 100.0, "currency": "VND"},
        headers={**_auth(t["a"]), "X-Organization-Id": org_id},
    )
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]
    assert r.json()["organization_id"] == org_id

    # Member B thấy hóa đơn A trong ngữ cảnh org
    org_list = client.get("/invoices", headers={**_auth(t["b"]), "X-Organization-Id": org_id}).json()
    assert [i["id"] for i in org_list] == [inv_id]

    # ... và không thấy trong ngữ cảnh cá nhân
    assert client.get("/invoices", headers=_auth(t["b"])).json() == []
    # A vẫn thấy trong cá nhân — spec: cá nhân = mọi bản ghi user từng ghi,
    # kể cả bản ghi org do chính mình tạo
    assert [i["id"] for i in client.get("/invoices", headers=_auth(t["a"])).json()] == [inv_id]

    # B đọc chi tiết + viewer cũng vậy
    h_b = {**_auth(t["b"]), "X-Organization-Id": org_id}
    h_v = {**_auth(t["v"]), "X-Organization-Id": org_id}
    assert client.get(f"/invoices/{inv_id}", headers=h_b).status_code == 200
    assert client.get(f"/invoices/{inv_id}", headers=h_v).status_code == 200


def test_viewer_read_only_in_org(client):
    org_id, t = _setup_org(client)
    h_v = {**_auth(t["v"]), "X-Organization-Id": org_id}
    assert client.get("/invoices", headers=h_v).status_code == 200
    r = client.post(
        "/invoices",
        json={"invoice_number": "V-1", "vendor": "V", "total": 1.0},
        headers=h_v,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_member_can_edit_other_members_org_invoice(client):
    org_id, t = _setup_org(client)
    r = client.post(
        "/invoices",
        json={"invoice_number": "ORG-002", "vendor": "Vendor", "total": 50.0},
        headers={**_auth(t["a"]), "X-Organization-Id": org_id},
    )
    inv_id = r.json()["id"]
    # B sửa được hóa đơn org của A (đúng mục tiêu dùng chung)
    r = client.put(
        f"/invoices/{inv_id}", json={"total": 60.0},
        headers={**_auth(t["b"]), "X-Organization-Id": org_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 60.0


def test_auto_match_does_not_cross_org_boundary(client):
    """Tiền cá nhân không tự ghép với hóa đơn công ty (và ngược lại)."""
    org_id, t = _setup_org(client)
    # Giao dịch cá nhân 30,900,000 đang chờ hóa đơn
    r = client.post(
        "/payments/transactions",
        json={"amount": 30900000, "currency": "VND", "merchant": "Personal"},
        headers=_auth(t["a"]),
    )
    assert r.status_code == 200, r.text

    # Hóa đơn org cùng số tiền — phải unmatched (không ghép chéo)
    r = client.post(
        "/invoices",
        json={"invoice_number": "ORG-BIG", "vendor": "Vendor", "total": 30900000, "currency": "VND"},
        headers={**_auth(t["a"]), "X-Organization-Id": org_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unpaid"

    tx = client.get("/payments/transactions", headers=_auth(t["a"])).json()[0]
    assert tx["status"] == "pending_receipt"

    # Giao dịch trong org số tiền KHÁC (để pool không ambiguous) → hóa đơn org
    # kế tiếp tự ghép trong org
    r = client.post(
        "/payments/transactions",
        json={"amount": 500000, "currency": "VND", "merchant": "Company"},
        headers={**_auth(t["a"]), "X-Organization-Id": org_id},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/invoices",
        json={"invoice_number": "ORG-SMALL", "vendor": "Vendor", "total": 500000, "currency": "VND"},
        headers={**_auth(t["a"]), "X-Organization-Id": org_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid"  # ghép với tx org
    # giao dịch cá nhân vẫn pending
    tx_personal = [
        x for x in client.get("/payments/transactions", headers=_auth(t["a"])).json()
        if x["merchant"] == "Personal"
    ][0]
    assert tx_personal["status"] == "pending_receipt"


def test_upload_in_org_context(client):
    """Upload với org header → hóa đơn thuộc org, member khác thấy, ghép tx org."""
    org_id, t = _setup_org(client)
    h_org_a = {**_auth(t["a"]), "X-Organization-Id": org_id}

    # Giao dịch org chờ hóa đơn (do A tạo)
    r = client.post(
        "/payments/transactions",
        json={"amount": 30900000, "currency": "VND", "merchant": "Company"},
        headers=h_org_a,
    )
    tx_id = r.json()["id"]

    data = open("tests/fixtures_gtgt.txt", "rb").read() if False else _GTGT_BYTES
    r = client.post(
        f"/upload?transaction_id={tx_id}",
        files={"file": ("hoadon.txt", data, "text/plain")},
        headers=h_org_a,
    )
    assert r.status_code == 202, r.text
    inv = r.json()
    assert inv["organization_id"] == org_id
    assert inv["job_status"] == "finalized"  # ghép với tx org tường minh
    assert inv["payment_match_status"] == "matched"

    # Member B thấy hóa đơn trong ngữ cảnh org
    org_list = client.get("/invoices", headers={**_auth(t["b"]), "X-Organization-Id": org_id}).json()
    assert [i["id"] for i in org_list] == [inv["id"]]


def test_worker_async_passthrough_org_id(client, monkeypatch):
    """WORKER_ENABLED=1: task nhận đủ (invoice_id, storage_key, user_id, tx_id, org_id)."""
    from src.workers import extraction_worker

    org_id, t = _setup_org(client)
    monkeypatch.setenv("WORKER_ENABLED", "1")
    enqueued = []

    class FakeTask:
        def delay(self, *args):
            enqueued.append(args)

    monkeypatch.setattr(extraction_worker, "extract_invoice", FakeTask())
    r = client.post(
        "/upload",
        files={"file": ("hoadon.txt", _GTGT_BYTES, "text/plain")},
        headers={**_auth(t["a"]), "X-Organization-Id": org_id},
    )
    assert r.status_code == 202, r.text
    assert len(enqueued) == 1
    assert enqueued[0][4] == org_id  # organization_id truyền qua worker


def test_delete_org_releases_data_to_personal(client):
    org_id, t = _setup_org(client)
    h_org_a = {**_auth(t["a"]), "X-Organization-Id": org_id}
    r = client.post(
        "/invoices",
        json={"invoice_number": "ORG-DEL", "vendor": "V", "total": 5.0},
        headers=h_org_a,
    )
    inv_id = r.json()["id"]

    # viewer không xóa được org
    r = client.delete(f"/organizations/{org_id}", headers=_auth(t["v"]))
    assert r.status_code == 403

    r = client.delete(f"/organizations/{org_id}", headers=_auth(t["a"]))
    assert r.status_code == 200, r.text

    # Hóa đơn trở về cá nhân của A
    personal = client.get("/invoices", headers=_auth(t["a"])).json()
    assert [i["id"] for i in personal] == [inv_id]
    assert personal[0]["organization_id"] is None

    # Org đã biến: header cũ → 404
    r = client.get("/invoices", headers=h_org_a)
    assert r.status_code == 404


_GTGT_BYTES = (
    "HÓA ĐƠN GIÁ TRỊ GIA TĂNG\n"
    "Số hóa đơn: 00012345\n"
    "Người bán: CÔNG TY TNHH ABC\n"
    "Cộng tiền hàng hóa, dịch vụ: 29,000,000\n"
    "Chiết khấu thương mại: 1,000,000\n"
    "Thuế GTGT: 2,900,000\n"
    "Tổng cộng tiền thanh toán: 30,900,000\n"
    "Ngày 04/08/2026\n"
).encode("utf-8")
