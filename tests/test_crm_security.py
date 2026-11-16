"""Tasks, webhooks (signed + SSRF-guarded), audit log, MFA, email_valid auto-check."""
import hashlib, hmac, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import mfa, webhooks
from app.db import current_ws, execute, query, query_one, utcnow
from app.main import app
from app.security import SESSION_COOKIE, hash_password, issue_token

ADMIN = ("admin", "pytest-admin")


@pytest.fixture()
def admin():
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(ADMIN[0]))
    return c


def _lead(admin, name="CRM Cafe"):
    r = admin.post("/api/search", json={"niche": "cafe", "city": "Pune", "provider": "mock", "max_results": 3})
    assert r.status_code == 200, r.text
    import time
    for _ in range(40):
        if admin.get(f"/api/search/{r.json()['search_id']}/status").json()["status"] != "RUNNING":
            break
        time.sleep(0.5)
    return admin.get("/api/leads", params={"limit": 5}).json()["leads"][0]["id"]


# ------------------------------------------------------------------ tasks ---
def test_task_lifecycle_and_overdue(admin):
    lid = _lead(admin)
    t = admin.post("/api/tasks", json={"title": "Call owner", "lead_id": lid,
                                       "due_at": "2000-01-01T00:00:00+00:00"}).json()
    assert t["status"] == "OPEN"
    assert any(x["id"] == t["id"] for x in admin.get("/api/tasks", params={"overdue": True}).json()["tasks"])
    done = admin.patch(f"/api/tasks/{t['id']}", json={"status": "DONE"}).json()
    assert done["status"] == "DONE" and done["completed_at"]
    assert not any(x["id"] == t["id"] for x in admin.get("/api/tasks").json()["tasks"])
    assert admin.delete(f"/api/tasks/{t['id']}").status_code == 200
    assert admin.post("/api/tasks", json={"title": "x", "lead_id": 99999999}).status_code == 404


def test_reply_creates_followup_task(admin):
    lid = _lead(admin)
    admin.post(f"/api/leads/{lid}/reply", json={"text": "Yes interested, can we talk Tuesday?"})
    tasks = admin.get("/api/tasks", params={"lead_id": lid}).json()["tasks"]
    assert tasks and "Confirm" in tasks[0]["title"]


# --------------------------------------------------------------- webhooks ---
def test_webhook_rejects_private_urls(admin):
    assert admin.post("/api/webhooks", json={"url": "http://127.0.0.1:9/x"}).status_code == 400
    assert admin.post("/api/webhooks", json={"url": "http://169.254.169.254/"}).status_code == 400
    assert admin.post("/api/webhooks", json={"url": "https://example.com/h", "events": ["nope"]}).status_code == 400


def test_webhook_signature_and_delivery(monkeypatch, admin):
    got = {}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.update(body=body, sig=self.headers["X-Signature"], event=self.headers["X-Event"])
            self.send_response(200); self.end_headers()
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # the local receiver is loopback, so bypass only the SSRF check for this test
    monkeypatch.setattr(webhooks.netsafe, "validate_url", lambda u: u)
    secret = webhooks.new_secret()
    hid = execute("INSERT INTO webhooks(workspace_id, url, secret, events, is_active, created_at) VALUES(?,?,?,?,1,?)",
                  (1, f"http://127.0.0.1:{srv.server_port}/", secret, "reply.received,ping", utcnow()))
    from app.db import workspace_context
    wid = query_one("SELECT workspace_id FROM webhooks WHERE id = ?", (hid,))["workspace_id"]
    with workspace_context(wid):
        n = webhooks.emit("reply.received", {"lead_id": 1}, wait=True)
        assert webhooks.emit("lead.hot", {}, wait=True) == 0          # not subscribed
    srv.shutdown()
    execute("DELETE FROM webhooks WHERE id = ?", (hid,))             # don't leak a hook into other tests
    assert n == 1 and got["event"] == "reply.received"
    assert got["sig"] == "sha256=" + hmac.new(secret.encode(), got["body"], hashlib.sha256).hexdigest()
    assert json.loads(got["body"])["data"] == {"lead_id": 1}
    assert query_one("SELECT status_code FROM webhook_deliveries WHERE webhook_id = ?", (hid,))["status_code"] == 200
    execute("DELETE FROM webhook_deliveries WHERE webhook_id = ?", (hid,))


# -------------------------------------------------------------- audit log ---
def test_audit_log_records_writes_not_reads_or_bodies(admin):
    admin.get("/api/leads")
    admin.post("/api/tasks", json={"title": "secret-title-should-not-be-logged"})
    entries = admin.get("/api/audit-log").json()["entries"]
    assert any(e["method"] == "POST" and e["path"] == "/api/tasks" and e["username"] == "admin" for e in entries)
    assert not any(e["method"] == "GET" for e in entries)
    assert "secret-title" not in json.dumps(entries)


# -------------------------------------------------------------------- MFA ---
def test_totp_matches_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, secret "12345678901234567890", T=59s -> 94287082 (last 6: 287082)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert mfa.code_at(secret, 59) == "287082"
    assert mfa.verify_code(secret, "287082", now=59)
    assert not mfa.verify_code(secret, "000000", now=59)
    assert not mfa.verify_code(secret, "287082", now=59 + 120)      # outside drift window


def test_mfa_login_flow():
    from app import security
    security._hits.clear()                      # the login limiter is per-IP and shared by the suite
    name = "mfa_user"
    execute("DELETE FROM users WHERE username = ?", (name,))
    uid = execute("INSERT INTO users(username, password_hash, role, is_active, created_at, updated_at) "
                  "VALUES(?,?,?,1,?,?)", (name, hash_password("password123"), "user", utcnow(), utcnow()))
    try:
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE, issue_token(name))
        secret = c.post("/api/auth/mfa/setup").json()["secret"]
        assert c.post("/api/auth/mfa/enable", json={"code": "000000"}).status_code == 400
        assert c.post("/api/auth/mfa/enable", json={"code": mfa.code_at(secret)}).status_code == 200

        fresh = TestClient(app)
        r = fresh.post("/api/auth/login", json={"username": name, "password": "password123"})
        assert r.status_code == 401 and r.headers.get("X-MFA-Required") == "1"
        bad = fresh.post("/api/auth/login", json={"username": name, "password": "password123", "code": "123456"})
        assert bad.status_code == 401
        ok = fresh.post("/api/auth/login", json={"username": name, "password": "password123",
                                                 "code": mfa.code_at(secret)})
        assert ok.status_code == 200
        assert c.post("/api/auth/mfa/disable", json={"code": mfa.code_at(secret)}).status_code == 200
        assert TestClient(app).post("/api/auth/login", json={"username": name,
                                                              "password": "password123"}).status_code == 200
    finally:
        execute("DELETE FROM users WHERE id = ?", (uid,))


# ------------------------------------------------------ email_valid fix -----
def test_unvalidated_email_is_checked_not_assumed_invalid(admin):
    from app.outreach import queue_message
    from app.db import workspace_context
    lid = _lead(admin)
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lid,))
    execute("UPDATE leads SET email = ?, email_valid = 0 WHERE id = ?", ("owner@realshop.in", lid))
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lid,))
    with workspace_context(lead["workspace_id"]):
        assert queue_message(lead, "cold_email_first", "s_fix")
    assert query_one("SELECT email_valid FROM leads WHERE id = ?", (lid,))["email_valid"] == 1
    execute("UPDATE leads SET email = ?, email_valid = 0 WHERE id = ?", ("not-an-email", lid))
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lid,))
    with workspace_context(lead["workspace_id"]):
        assert queue_message(lead, "cold_email_first", "s_fix2") is None
