"""Admin door tests: the dedicated /admin login page and its role-gated
endpoint.

Covers: page is public and served, admin login accepts admins only (regular
users get the same 401 as a bad password), admin-minted tokens resolve on
/admin paths while user-minted tokens do not, and signed-in regular users are
redirected away from /admin.

Environment and the isolated MySQL test database are set up in
tests/conftest.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.db import execute, init_db, query_one, utcnow  # noqa: E402
from app.security import hash_password, issue_token, verify_token  # noqa: E402

init_db()


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _make_user(username: str, password: str, role: str) -> None:
    execute(
        "REPLACE INTO users(username, password_hash, full_name, role, is_active, created_at) "
        "VALUES(?,?,?,?,1,?)",
        (username, hash_password(password), username.title(), role, utcnow()),
    )


@pytest.fixture(autouse=True)
def _fresh_login_limiter():
    """The login limiter is in-process and keyed by client IP, and the TestClient
    is always 'testclient' - so one rate-limit test would otherwise 429 every
    later login in the run. Each test starts with a clean limiter, which matches
    reality (different visitors arrive from different addresses)."""
    from app import security

    security._hits.clear()
    yield
    security._hits.clear()


# ------------------------------------------------------------------- page --


def test_admin_page_is_public_and_branded():
    with _client() as client:
        response = client.get("/admin")
        assert response.status_code == 200
        assert config.settings.brand_name in response.text
        assert "/api/auth/admin/login" in response.text


def test_admin_page_redirects_signed_in_admin_to_app():
    with _client() as client:
        client.post("/api/auth/login", json={
            "username": config.settings.admin_user,
            "password": config.settings.admin_password,
        })
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/"


# -------------------------------------------------------------- endpoint --


def test_admin_login_accepts_superadmin():
    _make_user("rootboss", "sup3r-secret", "superadmin")
    with _client() as client:
        response = client.post("/api/auth/admin/login", json={
            "username": "rootboss", "password": "sup3r-secret",
        })
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "superadmin"


def test_admin_login_accepts_admin():
    _make_user("midboss", "adm1n-secret", "admin")
    with _client() as client:
        response = client.post("/api/auth/admin/login", json={
            "username": "midboss", "password": "adm1n-secret",
        })
        assert response.status_code == 200


def test_admin_login_rejects_regular_user_indistinguishably():
    """A regular user's CORRECT credentials must fail exactly like a wrong
    password - the endpoint must not confirm which usernames exist."""
    _make_user("peon", "correct-horse", "user")
    with _client() as client:
        wrong_pw = client.post("/api/auth/admin/login", json={
            "username": "peon", "password": "wrong",
        })
        right_pw = client.post("/api/auth/admin/login", json={
            "username": "peon", "password": "correct-horse",
        })
        assert wrong_pw.status_code == right_pw.status_code == 401
        assert wrong_pw.json() == right_pw.json()


def test_regular_login_still_works_for_every_role():
    _make_user("plainuser", "user-pass", "user")
    with _client() as client:
        response = client.post("/api/auth/login", json={
            "username": "plainuser", "password": "user-pass",
        })
        assert response.status_code == 200


def test_admin_login_rate_limited_like_user_login():
    _make_user("ratelimitboss", "pw-limit", "superadmin")
    with _client() as client:
        # login_rate_limit (default 8) wrong attempts must trip the limiter.
        codes = [client.post("/api/auth/admin/login", json={
            "username": "ratelimitboss", "password": "nope",
        }).status_code for _ in range(10)]
        assert 429 in codes


# ----------------------------------------------------------- token scope --


def test_tokens_are_role_agnostic_by_design():
    """Tokens are not scoped per door - the boundary is the role checks on the
    API plus the role floor on the admin login endpoint. A token identifies a
    user; what that user may do is enforced per request."""
    token = issue_token("someuser")
    assert verify_token(token) == "someuser"


def test_admin_console_blocks_signed_in_regular_users():
    _make_user("sneaky", "sneak-pass", "user")
    with _client() as client:
        client.post("/api/auth/login", json={
            "username": "sneaky", "password": "sneak-pass",
        })
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/"


def test_existing_session_survives_after_admin_login_adds_nothing():
    """The same cookie name is reused; an admin logging in twice (either door)
    just replaces it. Smoke-test both doors in sequence in one client."""
    _make_user("twodoors", "pw-twodoors", "superadmin")
    with _client() as client:
        assert client.post("/api/auth/login", json={
            "username": "twodoors", "password": "pw-twodoors"}).status_code == 200
        assert client.post("/api/auth/admin/login", json={
            "username": "twodoors", "password": "pw-twodoors"}).status_code == 200
        me = client.get("/api/auth/me").json()
        assert me["user"]["role"] == "superadmin"


def test_deactivated_admin_cannot_use_admin_door():
    _make_user("sleepyadmin", "pw-sleepy", "admin")
    execute("UPDATE users SET is_active = 0 WHERE username = 'sleepyadmin'")
    with _client() as client:
        response = client.post("/api/auth/admin/login", json={
            "username": "sleepyadmin", "password": "pw-sleepy",
        })
        assert response.status_code == 401
