"""Phase 12: session revocation, CSRF origin guard, password reset."""
import pytest
from fastapi.testclient import TestClient

from app import passwordreset
from app.db import execute, query_one, utcnow
from app.main import app
from app.security import (SESSION_COOKIE, hash_password, issue_token, revoke_sessions,
                          verify_password, verify_token)


@pytest.fixture()
def user():
    name = "sess_user"
    execute("DELETE FROM users WHERE username = ?", (name,))
    uid = execute("INSERT INTO users(username, password_hash, email, role, is_active, created_at, updated_at) "
                  "VALUES(?,?,?,?,1,?,?)", (name, hash_password("oldpassword1"), "sess@example.com", "user",
                                            utcnow(), utcnow()))
    yield {"id": uid, "username": name}
    execute("DELETE FROM users WHERE id = ?", (uid,))


def test_revoke_invalidates_existing_tokens(user):
    token = issue_token(user["username"])
    assert verify_token(token) == user["username"]
    revoke_sessions(user["id"])
    assert verify_token(token) is None
    assert verify_token(issue_token(user["username"])) == user["username"]   # new login works


def test_logout_all_over_http(user):
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(user["username"]))
    assert c.get("/api/auth/me").status_code == 200
    assert c.post("/api/auth/logout-all").status_code == 200
    c.cookies.set(SESSION_COOKIE, "stale")
    other = TestClient(app)
    other.cookies.set(SESSION_COOKIE, issue_token(user["username"]))
    revoke_sessions(user["id"])
    assert other.get("/api/auth/me").status_code == 401


def test_cross_origin_write_blocked_same_origin_allowed(user):
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(user["username"]))
    evil = c.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    ok = c.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200


def test_password_reset_flow(user):
    c = TestClient(app)
    old_token = issue_token(user["username"])
    # unknown account answers identically (no enumeration)
    r = c.post("/api/auth/password-reset/request", json={"identifier": "nobody-here"})
    assert r.status_code == 200 and r.json()["ok"]

    token = passwordreset.request_reset("sess@example.com")      # by email
    assert token
    assert query_one("SELECT token_hash FROM password_resets WHERE user_id = ?",
                     (user["id"],))["token_hash"] != token        # stored hashed

    assert c.post("/api/auth/password-reset/confirm", json={"token": token, "password": "short"}).status_code == 400
    assert c.post("/api/auth/password-reset/confirm", json={"token": "bogus", "password": "longenough1"}).status_code == 400
    assert c.post("/api/auth/password-reset/confirm", json={"token": token, "password": "newpassword1"}).status_code == 200
    # single use
    assert c.post("/api/auth/password-reset/confirm", json={"token": token, "password": "another-pass1"}).status_code == 400

    row = query_one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
    assert verify_password("newpassword1", row["password_hash"])
    assert verify_token(old_token) is None                        # old sessions revoked


def test_expired_reset_token_rejected(user):
    token = passwordreset.create_token(user["id"])
    execute("UPDATE password_resets SET expires_at = '2000-01-01T00:00:00+00:00' WHERE user_id = ?", (user["id"],))
    assert passwordreset.confirm_reset(token, "newpassword1") is False
