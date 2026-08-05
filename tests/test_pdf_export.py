"""Test PDF export for monthly report."""
import pytest
from fastapi.testclient import TestClient
import src.app as app_module


def _register(c):
    r = c.post("/auth/register", json={"username": "pdf_test", "password": "test1234"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def client(monkeypatch):
    import os
    os.environ["OPEN_REGISTRATION"] = "1"
    from src.store.repository import InvoiceRepository
    from src.store.users import UserRepository
    monkeypatch.setattr(app_module, "repo", InvoiceRepository(":memory:"))
    monkeypatch.setattr(app_module, "users", UserRepository(":memory:"))
    with TestClient(app_module.app) as c:
        yield c


def test_pdf_export(client):
    h = _register(client)
    client.post("/invoices", json={
        "vendor": "Test Corp", "issue_date": "2026-07-15",
        "subtotal": 1000000, "tax": 100000, "total": 1100000, "status": "paid"
    }, headers=h)
    resp = client.get("/reports/monthly/2026-07/pdf", headers=h)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 1000

def test_pdf_no_invoices(client):
    h = _register(client)
    resp = client.get("/reports/monthly/2026-07/pdf", headers=h)
    assert resp.status_code == 404
