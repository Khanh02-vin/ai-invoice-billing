from datetime import UTC, datetime, timedelta
import warnings

from fastapi.testclient import TestClient


def test_app_lifespan_starts_and_stops_imap_poller(monkeypatch):
    import src.app as app_module

    calls = []
    monkeypatch.setattr(app_module._imap_poller, "start", lambda: calls.append("start"))
    monkeypatch.setattr(app_module._imap_poller, "stop", lambda: calls.append("stop"))

    assert app_module.app.router.on_startup == []
    assert app_module.app.router.on_shutdown == []
    with TestClient(app_module.app):
        assert calls == ["start"]
    assert calls == ["start", "stop"]


def test_sqlmodel_invoice_uses_canonical_domain_enums_without_warnings():
    from src.domain.models import InvoiceStatus as DomainInvoiceStatus
    from src.domain.models import JobStatus as DomainJobStatus
    from src.models import Invoice, InvoiceStatus, JobStatus

    assert InvoiceStatus is DomainInvoiceStatus
    assert JobStatus is DomainJobStatus
    invoice = Invoice(
        user_id="enum-test",
        status=DomainInvoiceStatus.UNPAID,
        job_status=DomainJobStatus.PROCESSING,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        invoice.model_dump()
    assert not [warning for warning in caught if "Expected `enum`" in str(warning.message)]


def test_legacy_invoice_repository_normalizes_extraction_provenance():
    from src.domain.models import FieldProvenance, Invoice
    from src.store.repository import InvoiceRepository

    invoice = Invoice(id="provenance-test", user_id="u1", invoice_number="P-1")
    invoice.provenance = {
        "total": {"value": 100, "confidence": 0.9, "source": "ocr"}
    }
    repo = InvoiceRepository(":memory:")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        saved = repo.upsert(invoice)
    assert isinstance(saved.provenance["total"], FieldProvenance)
    assert not [
        warning for warning in caught
        if "Expected `FieldProvenance`" in str(warning.message)
    ]


def test_legacy_token_repositories_accept_aware_expiry_values(tmp_path):
    from src.store.refresh_tokens import RefreshTokenRepository

    repo = RefreshTokenRepository(str(tmp_path / "legacy-tokens.db"))
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    repo.store_email_verification("verify-aware", "user-1", future)
    repo.store_password_reset("reset-aware", "user-1", future)

    assert repo.verify_email("verify-aware") == "user-1"
    assert repo.verify_password_reset("reset-aware") == "user-1"


def test_sqlmodel_token_repository_accepts_legacy_naive_expiries():
    from src.models import User
    from src.store.sql_repository import Database, UserRepository

    repo = UserRepository(Database(":memory:"))
    repo.db.create_tables()
    with repo.db.get_session() as session:
        session.add(User(id="user-1", username="user-one", password_hash="x" * 60))
        session.commit()
    naive_future = (datetime.now(UTC) + timedelta(days=2)).replace(tzinfo=None).isoformat()
    naive_past = "2000-01-01T00:00:00"
    repo.store_refresh_token("future-naive", "user-1", naive_future)
    repo.store_refresh_token("expired-naive", "user-1", naive_past)

    assert repo.is_refresh_token_valid("future-naive") == "user-1"
    assert repo.is_refresh_token_valid("expired-naive") is None


def test_payment_transaction_default_datetime_is_utc_aware():
    from src.domain.payments import PaymentTransaction

    assert PaymentTransaction().created_at.utcoffset().total_seconds() == 0


def test_domain_billing_timestamps_are_utc_aware():
    from src.domain.billing import CheckoutSession, Entitlement, Subscription, WebhookEvent

    for timestamp in (
        Subscription().created_at,
        Entitlement().updated_at,
        CheckoutSession().created_at,
        WebhookEvent().created_at,
    ):
        assert datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0


def test_ai_invoice_default_datetime_is_utc_aware():
    from invoice_ai.domain.models import Invoice

    assert Invoice().created_at.utcoffset().total_seconds() == 0
