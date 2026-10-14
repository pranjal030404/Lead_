"""Phase 1: SSRF guard, security headers, health probes, migration runner."""

import pytest
from fastapi.testclient import TestClient

from app import netsafe, website
from app.db import MIGRATIONS, query, run_migrations, get_conn
from app.main import app

BLOCKED = [
    "http://127.0.0.1/", "http://localhost/", "http://0.0.0.0/",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://[fe80::1]/", "http://100.64.0.1/",
    "http://metadata.google.internal/", "http://printer.local/",
    "http://2130706433/",          # decimal form of 127.0.0.1
    "ftp://example.com/", "file:///etc/passwd", "http://user:pw@example.com/",
    "http://example.com:3306/",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_validate_url_blocks_internal_targets(url):
    with pytest.raises(netsafe.UnsafeURL):
        netsafe.validate_url(url)


def test_public_literal_ip_allowed():
    assert netsafe.is_safe_url("https://93.184.216.34/")


def test_redirect_to_internal_address_is_blocked(monkeypatch):
    class R:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/"}
        content = b""

    calls = []
    monkeypatch.setattr(netsafe.http, "get", lambda url, **kw: calls.append(url) or R())
    with pytest.raises(netsafe.UnsafeURL):
        netsafe.safe_get("https://93.184.216.34/", breaker="t")
    assert calls == ["https://93.184.216.34/"]  # the metadata URL was never requested


def test_check_website_never_fetches_internal(monkeypatch):
    monkeypatch.setattr(netsafe.http, "get",
                        lambda *a, **k: pytest.fail("internal URL was fetched"))
    result = website.check_website("http://127.0.0.1:8080/")
    assert result["website_exists"] == 0
    assert "public internet address" in result["website_issues"][0]


def test_security_headers_on_public_and_error_responses():
    with TestClient(app) as client:
        for path in ("/health/live", "/api/leads"):  # second is a 401
            r = client.get(path)
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"


def test_health_probes():
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"] == {"mysql": "ok", "migrations": "ok"}


def test_migrations_recorded_and_idempotent():
    recorded = {r["version"] for r in query("SELECT version FROM schema_migrations")}
    assert recorded == {v for v, _, _ in MIGRATIONS}
    with get_conn() as conn:
        assert run_migrations(conn) == []
