"""Tests cho payments feature: parse SMS ngân hàng/ví + ingest + auto-match 2 chiều."""
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.config import get_settings
from src.domain.payments import TxDirection, TxStatus
from src.payments.imap_poller import (
    ImapPoller,
    build_event_text,
    parse_recipients,
    resolve_user_ids,
)
from src.payments.parser import parse_payment_text
from src.store.repository import InvoiceRepository
from src.store.transactions import TransactionRepository
from src.store.users import UserRepository


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Mỗi test dùng DB :memory: mới cho đủ 3 repo (invoice/users/tx)."""
    monkeypatch.setenv("OPEN_REGISTRATION", "1")
    app_module.repo = InvoiceRepository(db_path=":memory:")
    app_module.users = UserRepository(db_path=":memory:")
    app_module.tx_repo = TransactionRepository(db_path=":memory:")
    yield


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def _register(client, username="linh", password="matkhau123", email: str = None) -> str:
    payload = {"username": username, "password": password}
    if email:
        payload["email"] = email
    r = client.post("/auth/register", json=payload)
    assert r.status_code == 200, r.text
    app_module.users.set_verified(app_module.users.get_by_username(username).id, True)
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- Parser: SMS ngân hàng / ví ----------

def test_parse_vcb_sms_amount_not_balance():
    ev = parse_payment_text(
        "VCB - TK0071001234567 tru 350.000 VND luc 01/10/2026 14:33. "
        "SD: 10,000,000 VND. ND: WINMART QUAN 1"
    )
    assert ev is not None
    assert ev.amount == 350000
    assert ev.currency == "VND"
    assert ev.direction == TxDirection.DEBIT
    assert ev.merchant == "WINMART QUAN 1"
    assert ev.occurred_at == "2026-10-01T14:33:00"


def test_parse_acb_balance_skipped_keyword_fallback():
    # Số đầu tiên là số dư (SD) — parser phải nhảy qua và bắt số GD
    ev = parse_payment_text(
        "ACB: 10:30 01/10/2026 SD 15,588,004 VND GD -2,000,000 GDV:VNPAY*WINMART"
    )
    assert ev is not None
    assert ev.amount == 2000000
    assert ev.direction == TxDirection.DEBIT
    assert "WINMART" in ev.merchant


def test_parse_gdv_merchant_stops_before_balance():
    """Merchant không nuốt 'SD: ...' cùng dòng (bug hiển thị 'VINMART QUAN 1. SD: 15')."""
    ev = parse_payment_text(
        "ACB: GD -350,000 VND luc 01/10/2026 09:15. GDV:VINMART QUAN 1. SD: 15,588,004 VND"
    )
    assert ev is not None
    assert ev.amount == 350000
    assert ev.merchant == "VINMART QUAN 1"


def test_parse_merchant_stops_before_time_keyword():
    """Merchant không nuốt 'luc 20:15' cùng dòng."""
    ev = parse_payment_text("The: GD -120,000 VND. Thanh toan tai Circle K luc 20:15")
    assert ev is not None
    assert ev.amount == 120000
    assert ev.merchant == "Circle K"


def test_parse_momo_credit():
    ev = parse_payment_text("MoMo: Ban da nhan 500,000 VND tu NGUYEN VAN A. So du: 1,200,000 VND")
    assert ev is not None
    assert ev.amount == 500000
    assert ev.direction == TxDirection.CREDIT


def test_parse_momo_debit():
    ev = parse_payment_text(
        "Ban da thanh toan 120,000 VND thanh cong. So du: 1,234,000 VND"
    )
    assert ev is not None
    assert ev.amount == 120000
    assert ev.direction == TxDirection.DEBIT


def test_parse_usd_prefix_currency():
    ev = parse_payment_text("Card ending 1234 debited $250.00 at AMAZON")
    assert ev is not None
    assert ev.amount == 250.0
    assert ev.currency == "USD"


def test_parse_no_amount_returns_none():
    assert parse_payment_text("xin chao the gioi") is None
    assert parse_payment_text("") is None


# ---------- API: ingest ----------

def test_ingest_unparseable_422(client):
    token = _register(client)
    r = client.post(
        "/payments/ingest",
        json={"raw_text": "xin chao the gioi"},
        headers=_auth(token),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNPARSEABLE"


def test_ingest_credit_not_stored(client):
    token = _register(client)
    r = client.post(
        "/payments/ingest",
        json={"raw_text": "MoMo: Ban da nhan 500,000 VND tu NGUYEN VAN A"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["stored"] is False
    assert r.json()["reason"] == "credit_not_tracked"
    r2 = client.get("/payments/transactions", headers=_auth(token))
    assert r2.json() == []


def test_ingest_dedupe_same_text(client):
    token = _register(client)
    body = {"raw_text": "VCB tru 350.000 VND luc 01/10/2026 14:33"}
    r1 = client.post("/payments/ingest", json=body, headers=_auth(token))
    r2 = client.post("/payments/ingest", json=body, headers=_auth(token))
    assert r1.json()["stored"] is True
    assert r2.json()["duplicate"] is True
    assert r1.json()["transaction"]["id"] == r2.json()["transaction"]["id"]
    txs = client.get("/payments/transactions", headers=_auth(token)).json()
    assert len(txs) == 1


# ---------- Auto-match 2 chiều ----------

def test_ingest_matches_existing_invoice(client):
    token = _register(client)
    inv = client.post(
        "/invoices",
        json={"vendor": "WinMart", "total": 350000, "currency": "VND"},
        headers=_auth(token),
    ).json()
    assert inv["status"] == "unpaid"

    r = client.post(
        "/payments/ingest",
        json={"raw_text": "VCB tru 350.000 VND luc 01/10/2026"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["stored"] is True
    assert body["transaction"]["status"] == "matched"
    assert body["matched_invoice_id"] == inv["id"]

    # Hóa đơn chuyển sang paid
    invoices = client.get("/invoices", headers=_auth(token)).json()
    assert invoices[0]["status"] == "paid"


def test_invoice_after_ingest_auto_links(client):
    token = _register(client)
    r = client.post(
        "/payments/ingest",
        json={"raw_text": "MoMo: da thanh toan 120,000 VND tai WINMART"},
        headers=_auth(token),
    )
    assert r.json()["transaction"]["status"] == "pending_receipt"

    inv = client.post(
        "/invoices",
        json={"vendor": "WinMart", "total": 120000, "currency": "VND"},
        headers=_auth(token),
    ).json()
    assert inv["status"] == "paid"

    txs = client.get(
        "/payments/transactions?status=matched", headers=_auth(token)
    ).json()
    assert len(txs) == 1
    assert txs[0]["matched_invoice_id"] == inv["id"]


def test_amount_mismatch_stays_pending(client):
    token = _register(client)
    client.post(
        "/invoices",
        json={"vendor": "WinMart", "total": 99999, "currency": "VND"},
        headers=_auth(token),
    )
    r = client.post(
        "/payments/ingest",
        json={"raw_text": "VCB tru 350.000 VND luc 01/10/2026"},
        headers=_auth(token),
    )
    assert r.json()["transaction"]["status"] == "pending_receipt"
    assert r.json()["matched_invoice_id"] == ""


# ---------- Dismiss / manual match / isolation ----------

def test_dismiss_transaction(client):
    token = _register(client)
    r = client.post(
        "/payments/ingest",
        json={"raw_text": "VCB tru 88.000 VND luc 02/10/2026"},
        headers=_auth(token),
    )
    tx_id = r.json()["transaction"]["id"]
    r2 = client.post(f"/payments/transactions/{tx_id}/dismiss", headers=_auth(token))
    assert r2.json()["dismissed"] is True
    assert r2.json()["transaction"]["status"] == "dismissed"
    pending = client.get(
        "/payments/transactions?status=pending_receipt", headers=_auth(token)
    ).json()
    assert pending == []


def test_manual_match_allows_amount_mismatch(client):
    token = _register(client)
    tx_id = client.post(
        "/payments/ingest",
        json={"raw_text": "VCB tru 100.000 VND luc 02/10/2026"},
        headers=_auth(token),
    ).json()["transaction"]["id"]
    # Số tiền lệch hẳn -> auto-match không ghép
    inv = client.post(
        "/invoices",
        json={"vendor": "Khac", "total": 50000, "currency": "VND"},
        headers=_auth(token),
    ).json()
    assert inv["status"] == "unpaid"

    # User tự ghép -> chấp nhận dù lệch số tiền
    r = client.post(
        f"/payments/transactions/{tx_id}/match",
        json={"invoice_id": inv["id"]},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["matched"] is True
    assert r.json()["transaction"]["status"] == "matched"


def test_multi_user_isolation(client):
    token_a = _register(client, username="usera")
    tx_id = client.post(
        "/payments/ingest",
        json={"raw_text": "VCB tru 77.000 VND luc 02/10/2026"},
        headers=_auth(token_a),
    ).json()["transaction"]["id"]

    token_b = _register(client, username="userb")
    assert client.get("/payments/transactions", headers=_auth(token_b)).json() == []
    r = client.post(
        f"/payments/transactions/{tx_id}/dismiss", headers=_auth(token_b)
    )
    assert r.status_code == 404


# ---------- IMAP poller ----------

def _raw_email(subject: str, body: str, msg_id: str = "<tx1@vcb.com>", frm: str = "notify@vcb.com") -> bytes:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = frm
    m["Message-ID"] = msg_id
    m.set_content(body)
    return m.as_bytes()


def test_imap_config_toggle(monkeypatch):
    assert get_settings().imap_enabled is False
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "me@example.com")
    assert get_settings().imap_enabled is True


def test_build_event_text_parses_bank_email():
    raw = _raw_email(
        "Thong bao giao dich thanh cong",
        "Tai khoan 0123456789 tru 450.000 VND luc 02/10/2026 09:15. "
        "SD: 9,000,000 VND. ND: COOPMART QUAN 3",
    )
    import email as email_lib
    ev = parse_payment_text(build_event_text(email_lib.message_from_bytes(raw)), source="email")
    assert ev is not None
    assert ev.amount == 450000
    assert ev.direction == TxDirection.DEBIT
    assert ev.source == "email"


def test_build_event_text_html_body_fallback():
    m = EmailMessage()
    m["Subject"] = "MoMo thanh toan 88.000 VND"
    m["From"] = "no-reply@momo.vn"
    m.set_content("dummy plain")
    m.add_alternative("<p>Ban da <b>thanh toan 88.000 VND</b> tai FPT Shop</p>", subtype="html")
    ev = parse_payment_text(build_event_text(m), source="email")
    assert ev is not None
    assert ev.amount == 88000


def test_parse_recipients_formats():
    # Hỗ trợ: "email" (identity), "email=username", "email:username"
    assert parse_recipients("a@x.com,b@c.com:binh,d@x.com=eve") == {
        "a@x.com": "a@x.com",
        "b@c.com": "binh",
        "d@x.com": "eve",
    }
    assert parse_recipients("") == {}


def test_resolve_user_ids_map(client):
    token = _register(client, username="linh")
    linh = app_module.users.get_by_username("linh")
    m = EmailMessage()
    m["From"] = "<linh.bank@gmail.com>"
    mapping = {"linh.bank@gmail.com": "linh"}
    assert resolve_user_ids(m, app_module.users, mapping) == [linh.id]
    # Sai user trong map -> không gán ai
    assert resolve_user_ids(m, app_module.users, {"khac@x.com": "khac"}) == []


def test_poll_once_ingest_and_dedupe(client):
    token = _register(client)
    poller = ImapPoller(
        user_repo=app_module.users,
        tx_repo=app_module.tx_repo,
        invoice_repo_getter=lambda: app_module.repo,
    )
    raw = _raw_email("GD thanh cong", "tru 450.000 VND luc 02/10/2026 ND: COOPMART")
    marked: list = []

    def fake_fetch():
        return ([(b"7", raw)], lambda nums: marked.extend(nums))

    assert poller.poll_once(fetch_fn=fake_fetch) == 1
    assert marked == [b"7"]
    txs = client.get("/payments/transactions", headers=_auth(token)).json()
    assert len(txs) == 1
    assert txs[0]["amount"] == 450000
    assert txs[0]["source"] == "email"

    # Đọc lại cùng Message-ID -> dedupe, không tạo bản trùng
    poller.poll_once(fetch_fn=fake_fetch)
    txs = client.get("/payments/transactions", headers=_auth(token)).json()
    assert len(txs) == 1


def test_poll_once_marks_garbage_and_credit_seen(client):
    token = _register(client)
    poller = ImapPoller(
        user_repo=app_module.users,
        tx_repo=app_module.tx_repo,
        invoice_repo_getter=lambda: app_module.repo,
    )
    msgs = [
        (b"1", _raw_email("Ung dung", "khong co so tien o day", msg_id="<g1@x>")),
        (b"2", _raw_email("Nhan tien", "Ban da nhan 900,000 VND tu A", msg_id="<c1@x>")),
    ]
    marked: list = []

    def fake_fetch():
        return (msgs, lambda nums: marked.extend(nums))

    assert poller.poll_once(fetch_fn=fake_fetch) == 0
    # Email không phải giao dịch vẫn được đánh dấu đã đọc — không re-read mỗi vòng
    assert marked == [b"1", b"2"]
    assert client.get("/payments/transactions", headers=_auth(token)).json() == []


def test_poll_once_keeps_email_unseen_on_error(client):
    token = _register(client)

    def boom():
        raise RuntimeError("db down")

    poller = ImapPoller(
        user_repo=app_module.users,
        tx_repo=app_module.tx_repo,
        invoice_repo_getter=boom,
    )
    raw = _raw_email("GD", "tru 450.000 VND ND: COOPMART", msg_id="<e1@x>")
    marked: list = []

    def fake_fetch():
        return ([(b"9", raw)], lambda nums: marked.extend(nums))

    # Xử lý lỗi -> giữ lại email (không \\Seen) để chu kỳ sau retry
    assert poller.poll_once(fetch_fn=fake_fetch) == 0
    assert marked == []


def test_resolve_user_ids_by_email_column(client):
    token_a = _register(client, username="usera", email="linh.bank@gmail.com")
    token_b = _register(client, username="userb", email="khac@gmail.com")
    a = app_module.users.get_by_username("usera")
    b = app_module.users.get_by_username("userb")

    m = EmailMessage()
    m["From"] = "<linh.bank@gmail.com>"
    # Match cột email -> chỉ user A, không broadcast
    assert resolve_user_ids(m, app_module.users, {}) == [a.id]

    # Sender lạ nhưng không map -> broadcast mọi verified (inbox cá nhân)
    m2 = EmailMessage()
    m2["From"] = "<nobody@x.com>"
    assert sorted(resolve_user_ids(m2, app_module.users, {})) == sorted([a.id, b.id])


def test_register_with_email_shown_in_me(client):
    token = _register(client, username="mekho", email="mekho@example.com")
    me = client.get("/auth/me", headers=_auth(token)).json()
    assert me["email"] == "mekho@example.com"


def test_notify_disabled_silently_skips(client):
    from src.payments.notify import send_receipt_reminder
    from src.domain.payments import PaymentTransaction

    token = _register(client, username="notifyu", email="x@example.com")
    user = app_module.users.get_by_username("notifyu")
    tx = PaymentTransaction(user_id=user.id, amount=1000, currency="VND")
    # Không cấu hình SMTP -> không gửi, không lỗi
    assert send_receipt_reminder(user, tx) is False
    # User không có email cũng vậy
    user_no_email = user.model_copy(update={"email": "", "username": "plainuser"})
    assert send_receipt_reminder(user_no_email, tx) is False


def test_notify_sends_via_smtp(monkeypatch, client):
    from src.payments import notify
    from src.domain.payments import PaymentTransaction

    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def ehlo(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent.append(msg)

    # Đăng ký user TRƯỚC khi bật SMTP để register không gửi verification email.
    token = _register(client, username="meoluon", email="me@example.com")
    user = app_module.users.get_by_username("meoluon")

    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_SMTP_USER", "noreply@test")
    monkeypatch.setenv("EMAIL_FROM", "noreply@test")
    monkeypatch.setattr("src.mail.smtplib.SMTP", FakeSMTP)

    tx = PaymentTransaction(
        user_id=user.id, amount=350000, currency="VND", merchant="WINMART"
    )
    assert notify.send_receipt_reminder(user, tx) is True
    assert len(sent) == 1
    assert "350,000 VND" in sent[0]["Subject"]
    assert sent[0]["To"] == "me@example.com"
    assert "WINMART" in sent[0].get_content().decode() if isinstance(sent[0].get_content(), bytes) else "WINMART" in sent[0].get_content()
