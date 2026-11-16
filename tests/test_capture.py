"""Inbound capture form (/c/<slug>): needs the test database like the other tests."""
from fastapi.testclient import TestClient

from app.db import query_one, workspace_context
from app.main import app
from app.tenancy import default_workspace_id


def _slug():
    return query_one("SELECT slug FROM workspaces WHERE id = ?", (default_workspace_id(),))["slug"]


def test_form_is_public_and_creates_hot_lead():
    c = TestClient(app)
    slug = _slug()
    assert c.get(f"/c/{slug}").status_code == 200
    r = c.post(f"/c/{slug}", json={"name": "Asha", "business": "Asha Sweets",
                                   "email": "asha@ashasweets.in", "utm_source": "ig"})
    assert r.status_code == 200
    with workspace_context(default_workspace_id()):
        lead = query_one("SELECT * FROM leads WHERE email = ?", ("asha@ashasweets.in",))
    assert lead and lead["source"] == "Inbound form" and lead["lead_priority"] == "HOT"
    assert "utm_source=ig" in lead["notes"]


def test_honeypot_and_validation_and_unknown_slug():
    c = TestClient(app)
    slug = _slug()
    c.post(f"/c/{slug}", json={"name": "Bot", "email": "bot@spam.io", "company_url": "x"})
    with workspace_context(default_workspace_id()):
        assert not query_one("SELECT id FROM leads WHERE email = ?", ("bot@spam.io",))
    assert c.post(f"/c/{slug}", json={"name": "NoContact"}).status_code == 400
    assert c.get("/c/does-not-exist").status_code == 404


def test_growth_console_is_admin_only_and_switch_closes_form():
    anon = TestClient(app)
    assert anon.get("/api/growth").status_code == 401
    admin = TestClient(app)
    assert admin.post("/api/auth/admin/login",
                      json={"username": "admin", "password": "pytest-admin"}).status_code == 200
    data = admin.get("/api/growth").json()
    assert set(data) >= {"settings", "capture", "finder", "qualification", "sources"}
    assert admin.put("/api/growth/settings", json={"mql_threshold": 90, "sql_threshold": 10}).status_code == 400
    admin.put("/api/growth/settings", json={"capture_enabled": False})
    assert anon.get(f"/c/{_slug()}").status_code == 404
    admin.put("/api/growth/settings", json={"capture_enabled": True})
    assert anon.get(f"/c/{_slug()}").status_code == 200
