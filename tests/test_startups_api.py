"""Startups API + registry checker, against the real DB layer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import registry_check
from app.db import current_ws, execute, query_one, utcnow, workspace_context
from app.main import app
from app.security import SESSION_COOKIE, hash_password, issue_token
from app.tenancy import add_member, create_workspace


def _lead(ws, name, place_id, **extra):
    cols = {"workspace_id": ws, "business_name": name, "place_id": place_id, "city": "London",
            "country": "United Kingdom", "status": "NEW", "business_status": "OPERATIONAL",
            "source": "Companies House bulk (incorporated 2026-10-01, 62012)",
            "address": "old address", "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
                   tuple(cols.values()))


def _client(name):
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(name))
    return c


def _setup():
    ws = create_workspace("Startups WS")
    execute("REPLACE INTO users(username, password_hash, role, is_active, created_at) "
            "VALUES('st_admin',?,'admin',1,?)", (hash_password("x"), utcnow()))
    add_member(ws, query_one("SELECT id FROM users WHERE username='st_admin'")["id"], "owner")
    return ws, _client("st_admin")


def test_list_and_filter():
    ws, c = _setup()
    with workspace_context(ws):
        _lead(ws, "Alpha Labs Ltd", "ch:111")
        _lead(ws, "Bharat Tech Pvt Ltd", "mca:U1", country="India",
              source="India MCA (CIN U1, registered 2026-09-05)")
        _lead(ws, "Cafe Not Registry", "osm:1")
    r = c.get("/api/startups").json()
    names = {s["business_name"]: s for s in r["startups"]}
    assert set(names) == {"Alpha Labs Ltd", "Bharat Tech Pvt Ltd"}      # non-registry lead excluded
    assert names["Alpha Labs Ltd"]["registry"] == "uk" and names["Alpha Labs Ltd"]["registered_on"] == "2026-10-01"
    assert names["Bharat Tech Pvt Ltd"]["registry"] == "india" and names["Bharat Tech Pvt Ltd"]["registration_number"] == "U1"
    assert [s["business_name"] for s in c.get("/api/startups?country=India").json()["startups"]] == ["Bharat Tech Pvt Ltd"]


def test_check_dry_run_then_apply(monkeypatch):
    ws, c = _setup()
    with workspace_context(ws):
        dead = _lead(ws, "Dead Untouched Ltd", "ch:900")
        worked = _lead(ws, "Dead Worked Ltd", "ch:901", status="CONTACTED", total_touchpoints=3)
        moved = _lead(ws, "Moved Ltd", "ch:902")
        gone = _lead(ws, "Unknown Ltd", "ch:903")
    monkeypatch.setattr(registry_check, "uk_lookup", lambda nums: {
        "900": {"status": "Dissolved", "address": "x"},
        "901": {"status": "Liquidation", "address": "x"},
        "902": {"status": "Active", "address": "1 New Street, London, E1 1AA"}})

    dry = c.post("/api/startups/check").json()                    # default = dry run
    assert dry["dry_run"] is True and (dry["removed"], dry["retired"], dry["updated"]) == (1, 1, 1)
    with workspace_context(ws):
        assert query_one("SELECT id FROM leads WHERE id = ?", (dead,))      # nothing changed

    real = c.post("/api/startups/check?dry_run=false").json()
    assert (real["removed"], real["retired"], real["updated"], real["unknown"]) == (1, 1, 1, 1)
    with workspace_context(ws):
        assert query_one("SELECT id FROM leads WHERE id = ?", (dead,)) is None        # removed
        assert query_one("SELECT business_status FROM leads WHERE id = ?", (worked,))["business_status"] == "CLOSED_PERMANENTLY"
        assert query_one("SELECT address FROM leads WHERE id = ?", (moved,))["address"].startswith("1 New Street")
        assert query_one("SELECT business_status FROM leads WHERE id = ?", (gone,))["business_status"] == "OPERATIONAL"
    # closed leads are hidden from the list unless asked for
    shown = {s["business_name"] for s in c.get("/api/startups").json()["startups"]}
    assert "Dead Worked Ltd" not in shown
    assert "Dead Worked Ltd" in {s["business_name"] for s in c.get("/api/startups?closed=true").json()["startups"]}


def test_owners_uk_and_india(monkeypatch):
    ws, c = _setup()
    with workspace_context(ws):
        uk = _lead(ws, "Owner Ltd", "ch:777")
        ind = _lead(ws, "Desi Pvt Ltd", "mca:U9", country="India")
    assert c.post(f"/api/startups/{ind}/owners").status_code == 400          # no director data in India dataset
    monkeypatch.setattr(registry_check, "uk_officers",
                        lambda n: [{"name": "Jane Smith", "role": "director", "appointed_on": "2026-01-01"}])
    r = c.post(f"/api/startups/{uk}/owners").json()
    assert r["officers"][0]["name"] == "Jane Smith"
    with workspace_context(ws):
        assert query_one("SELECT owner_name FROM leads WHERE id = ?", (uk,))["owner_name"] == "Jane Smith"


def test_find_starts_both_registries(monkeypatch):
    ws, c = _setup()
    calls = []
    from app.routers import startups
    monkeypatch.setattr(startups, "start_search", lambda **kw: calls.append(kw) or len(calls))
    monkeypatch.setattr(startups, "check_search_entitlement", lambda u: {"ok": True})
    r = c.post("/api/startups/find", json={"niche": "software", "country": "both"}).json()
    assert [(k["provider_name"], k["country"]) for k in calls] == [
        ("web_ai", "United Kingdom"), ("web_ai", "India")] and r["status"] == "RUNNING"
    calls.clear()
    c.post("/api/startups/find", json={"country": "Germany"})
    assert [k["country"] for k in calls] == ["Germany"]
    calls.clear()
    c.post("/api/startups/find", json={"country": "worldwide"})
    assert [k["country"] for k in calls] == [""]


def test_startups_page_needs_login_and_renders():
    ws, c = _setup()
    assert c.get("/startups", follow_redirects=False).headers["location"] == "/#/startups"
    assert 'href="#/startups"' in c.get("/").text
    anon = TestClient(app).get("/startups", follow_redirects=False)
    assert anon.status_code in (302, 307, 401)
