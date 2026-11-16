"""Login by username OR email. Uses the MySQL test DB like the other DB tests (conftest)."""
import pytest

from app import security
from app.db import execute, init_db, query_one, utcnow


@pytest.fixture(autouse=True)
def _users():
    init_db()
    execute("DELETE FROM users WHERE username LIKE 'tlogin_%'")
    now = utcnow()
    for u, e, active in (("tlogin_amy", "Amy.Test@Example.com", 1), ("tlogin_bob", "shared@example.com", 1),
                         ("tlogin_cat", "shared@example.com", 1), ("tlogin_off", "off@example.com", 0),
                         ("tlogin_noemail", "", 1)):
        execute("INSERT INTO users(username,password_hash,full_name,email,role,is_active,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)", (u, security.hash_password("pw-" + u), u, e, "user", active, now, now))
    yield
    execute("DELETE FROM users WHERE username LIKE 'tlogin_%'")


def test_username_and_email_both_work_case_insensitively():
    assert security.authenticate_user("tlogin_amy", "pw-tlogin_amy")["username"] == "tlogin_amy"
    assert security.authenticate_user("amy.test@example.com", "pw-tlogin_amy")["username"] == "tlogin_amy"
    assert security.authenticate_user("  AMY.TEST@EXAMPLE.COM ", "pw-tlogin_amy")["username"] == "tlogin_amy"


def test_wrong_password_unknown_and_blank_fail():
    assert security.authenticate_user("amy.test@example.com", "nope") is None
    assert security.authenticate_user("ghost@example.com", "x") is None
    assert security.authenticate_user("", "") is None
    assert security.authenticate_user("@", "x") is None


def test_ambiguous_email_refuses_but_username_still_works():
    assert security.resolve_login("shared@example.com") is None
    assert security.authenticate_user("shared@example.com", "pw-tlogin_bob") is None
    assert security.authenticate_user("tlogin_bob", "pw-tlogin_bob") is not None


def test_inactive_and_blank_email_accounts():
    assert security.authenticate_user("off@example.com", "pw-tlogin_off") is None
    assert security.account_status("off@example.com") == "inactive"
    assert security.account_status("tlogin_off") == "inactive"
    assert security.account_status("nobody@example.com") is None
    assert security.authenticate_user("tlogin_noemail", "pw-tlogin_noemail") is not None
