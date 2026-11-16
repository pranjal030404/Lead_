"""Per-user feature permissions: enforced centrally, immediate, never applied to admins."""
import pytest
from fastapi.testclient import TestClient

from app import permissions
from app.db import execute, query_one, utcnow
from app.main import app
from app.security import SESSION_COOKIE, hash_password, issue_token
from app.tenancy import add_member

WS = None


@pytest.fixture()
def api():
    global WS
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token("admin"))
    WS = query_one("SELECT workspace_id FROM workspace_members ORDER BY workspace_id LIMIT 1")["workspace_id"]
    return c


def _user(name, role="user"):
    execute("DELETE FROM users WHERE username = ?", (name,))
    uid = execute("INSERT INTO users(username, password_hash, role, is_active, created_at, updated_at) VALUES(?,?,?,1,?,?)",
                  (name, hash_password("password123"), role, utcnow(), utcnow()))
    add_member(WS, uid, "member")
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(name))
    return uid, c


def test_parse_and_matching_rules():
    assert permissions.parse('["outreach", "bogus", "outreach", "tasks"]') == ["outreach", "tasks"]
    assert permissions.parse(None) == [] and permissions.parse("not json") == [] and permissions.parse(["meetings"]) == ["meetings"]
    u = {"role": "user", "denied_features": ["proposals"]}
    assert permissions.blocked_feature(u, "/api/proposals") == "proposals"
    assert permissions.blocked_feature(u, "/api/proposals/5/pdf") == "proposals"
    assert permissions.blocked_feature(u, "/api/proposalsX") is None               # path boundary, not a raw prefix
    assert permissions.blocked_feature(u, "/api/leads") is None
    assert permissions.blocked_feature({"role": "admin", "denied_features": ["proposals"]}, "/api/proposals") is None
    assert permissions.blocked_feature(None, "/api/proposals") is None


def test_denied_feature_is_blocked_immediately_and_only_that_feature(api):
    uid, user = _user("perm_user")
    try:
        assert user.get("/api/approvals").status_code == 200
        assert user.get("/api/proposals").status_code == 200
        r = api.patch(f"/api/users/{uid}/permissions", json={"denied": ["outreach", "proposals"]})
        assert r.status_code == 200 and sorted(r.json()["denied_features"]) == ["outreach", "proposals"]
        # same session, no re-login: takes effect on the very next request
        for path in ("/api/approvals", "/api/templates", "/api/proposals", "/api/proposals/revenue"):
            blocked = user.get(path)
            assert blocked.status_code == 403 and "don't have access" in blocked.json()["detail"], path
        assert user.post("/api/proposals", json={}).status_code == 403              # writes too
        assert user.get("/api/leads").status_code == 200                              # everything else untouched
        assert user.get("/api/tasks").status_code == 200 and user.get("/api/campaigns").status_code == 200
        me = user.get("/api/auth/me").json()["user"]
        assert sorted(me["denied_features"]) == ["outreach", "proposals"]               # the UI hides menu items from this
        listed = next(u for u in api.get("/api/users").json()["rows"] if u["id"] == uid)
        assert sorted(listed["denied_features"]) == ["outreach", "proposals"]
        api.patch(f"/api/users/{uid}/permissions", json={"denied": []})              # clear -> full access again
        assert user.get("/api/approvals").status_code == 200
    finally:
        execute("DELETE FROM users WHERE id = ?", (uid,))


def test_validation_and_who_may_change_permissions(api):
    uid, user = _user("perm_target")
    aid, other_admin = _user("perm_admin2", "admin")
    try:
        assert api.patch(f"/api/users/{uid}/permissions", json={"denied": ["nope"]}).status_code == 400
        assert api.patch(f"/api/users/{aid}/permissions", json={"denied": ["tasks"]}).status_code == 400   # admins can't be restricted
        assert api.patch("/api/users/99999999/permissions", json={"denied": []}).status_code == 404
        assert user.patch(f"/api/users/{uid}/permissions", json={"denied": []}).status_code == 403          # users can't edit permissions
        assert user.get("/api/permissions/features").status_code == 403
        feats = api.get("/api/permissions/features").json()["features"]
        assert {"outreach", "proposals", "campaigns"} <= {f["key"] for f in feats}
        # even if a restriction is forced onto an admin row directly, admins are never blocked
        execute("UPDATE users SET denied_features = ? WHERE id = ?", ('["proposals"]', aid))
        assert other_admin.get("/api/proposals").status_code == 200
    finally:
        execute("DELETE FROM users WHERE id IN (?, ?)", (uid, aid))


def test_viewer_and_permissions_stack(api):
    uid, viewer = _user("perm_viewer", "viewer")
    try:
        api.patch(f"/api/users/{uid}/permissions", json={"denied": ["meetings"]})
        assert viewer.get("/api/meetings").status_code == 403                          # denied feature
        assert viewer.get("/api/tasks").status_code == 200                              # viewers can read the rest
        assert viewer.post("/api/tasks", json={"title": "x"}).status_code == 403        # ...but not write
    finally:
        execute("DELETE FROM users WHERE id = ?", (uid,))
