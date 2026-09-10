"""Phase 4 Observability tests: metrics exposition via real app."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.app import app
from src.observability import metrics as obs_metrics


def test_metrics_endpoint_returns_200_and_base_metric():
    obs_metrics.reset_metrics()
    obs_metrics.track_request("GET", "/health", 200, 0.005)
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert "http_requests_total" in body
    assert 'method="GET"' in body
    assert 'path="/health"' in body


def test_track_request_increments_counter():
    obs_metrics.reset_metrics()
    obs_metrics.track_request("POST", "/api/invoices", 201, 0.123)
    obs_metrics.track_request("POST", "/api/invoices", 201, 0.456)
    text = obs_metrics.generate_metrics_text()
    assert "http_requests_total" in text
    assert 'method="POST"' in text
    assert 'status="201"' in text
    assert "http_request_duration_seconds_count 2" in text
