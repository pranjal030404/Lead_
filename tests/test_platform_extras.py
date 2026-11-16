"""Viewer role, saved segments, durable job queue (+webhook retries), proposal PDF, exports."""
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import jobqueue, webhooks
from app.db import execute, query, query_one, utcnow, workspace_context
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


def _user(name, role):
    execute("DELETE FROM users WHERE username = ?", (name,))
    uid = execute("INSERT INTO users(username, password_hash, role, is_active, created_at, updated_at) VALUES(?,?,?,1,?,?)",
                  (name, hash_password("password123"), role, utcnow(), utcnow()))
    add_member(WS, uid, "member")
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(name))
    return uid, c


def _lead(name, owner=0, **extra):
    cols = {"workspace_id": WS, "business_name": name, "city": "Pune", "niche": "cafes", "lead_score": 6,
            "lead_priority": "WARM", "status": "NEW", "email": f"{name.split()[0].lower()}@shop.example",
            "email_valid": 1, "owner_user_id": owner, "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})", tuple(cols.values()))


# ============================================================== viewer role ===
def test_viewer_reads_everything_writes_nothing(api):
    uid, viewer = _user("viewer_a", "viewer")
    try:
        _lead("Viewer Visible Cafe", owner=0)                         # owned by nobody in particular
        names = [l["business_name"] for l in viewer.get("/api/leads", params={"limit": 500}).json()["leads"]]
        assert "Viewer Visible Cafe" in names                          # a normal user would not see this one
        assert viewer.get("/api/pipeline").status_code == 200
        for method, path, body in (("post", "/api/tasks", {"title": "x"}), ("post", "/api/campaigns", {}),
                                   ("patch", "/api/leads/1", {}), ("delete", "/api/leads/1", None),
                                   ("post", "/api/search", {"niche": "a", "city": "b"})):
            r = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
            assert r.status_code == 403 and "read-only" in r.json()["detail"], (path, r.status_code)
        assert viewer.post("/api/auth/logout").status_code == 200       # self-service stays allowed
        assert viewer.get("/api/webhooks").status_code == 403           # not an admin
    finally:
        execute("DELETE FROM users WHERE id = ?", (uid,))


def test_regular_user_still_sees_only_own_leads(api):
    uid, user = _user("plain_user", "user")
    try:
        _lead("Not Yours Cafe", owner=0)
        mine = _lead("Mine Cafe", owner=uid)
        names = [l["business_name"] for l in user.get("/api/leads", params={"limit": 500}).json()["leads"]]
        assert "Mine Cafe" in names and "Not Yours Cafe" not in names
    finally:
        execute("DELETE FROM users WHERE id = ?", (uid,))


def test_admin_can_create_viewer_via_users_api(api):
    execute("DELETE FROM users WHERE username = 'made_viewer'")
    r = api.post("/api/users", json={"username": "made_viewer", "password": "password123", "role": "viewer"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "viewer"
    execute("DELETE FROM users WHERE username = 'made_viewer'")


# ================================================================= segments ===
def test_segment_lifecycle_and_campaign_enrol(api):
    _lead("Seg Alpha", niche="seg-niche", lead_score=8, lead_priority="HOT")
    _lead("Seg Beta", niche="seg-niche", lead_score=3, lead_priority="COLD")
    _lead("Seg Other", niche="other-niche", lead_priority="HOT")
    assert api.post("/api/segments", json={"name": "empty", "filters": {}}).status_code == 400
    r = api.post("/api/segments", json={"name": "Hot seg", "filters": {"niche": "seg-niche", "priority": "HOT"}})
    assert r.status_code == 200, r.text
    seg = r.json()
    assert seg["count"] == 1
    assert api.post("/api/segments", json={"name": "Hot seg", "filters": {"niche": "x"}}).status_code == 409
    assert [l["business_name"] for l in api.get(f"/api/segments/{seg['id']}/leads").json()["leads"]] == ["Seg Alpha"]
    assert api.post("/api/segments", json={"name": "bad", "filters": {"priority": "LUKEWARM"}}).status_code == 422

    camp = api.post("/api/campaigns", json={"name": "seg camp", "steps": [{"template": "cold_email_first"}]}).json()
    out = api.post(f"/api/campaigns/{camp['id']}/enroll", json={"segment_id": seg["id"]}).json()
    assert out["enrolled"] == 1
    assert api.post(f"/api/campaigns/{camp['id']}/enroll", json={"segment_id": 99999999}).status_code == 404
    assert api.delete(f"/api/segments/{seg['id']}").status_code == 200
    assert api.get(f"/api/segments/{seg['id']}/leads").status_code == 404


def test_segments_are_private_to_their_owner(api):
    uid, other = _user("seg_other", "user")
    try:
        seg = api.post("/api/segments", json={"name": "admin only", "filters": {"niche": "zzz"}}).json()
        # admins see everything; a regular user does not see another user's segment
        assert other.get(f"/api/segments/{seg['id']}/leads").status_code == 404
        assert seg["id"] not in [s["id"] for s in other.get("/api/segments").json()["segments"]]
    finally:
        execute("DELETE FROM users WHERE id = ?", (uid,))


# ================================================================ job queue ===
def _drain():
    execute("DELETE FROM job_queue WHERE kind LIKE 'test.%'")


def test_queue_success_retry_backoff_and_failure():
    _drain()
    calls = []

    @jobqueue.handler("test.flaky")
    def flaky(payload):
        calls.append(payload["n"])
        if len(calls) < 3:
            raise RuntimeError("boom")

    jid = jobqueue.enqueue("test.flaky", {"n": 1}, max_attempts=4)
    assert jobqueue.run_one(jid) == "RETRY"
    row = query_one("SELECT * FROM job_queue WHERE id = ?", (jid,))
    assert row["status"] == "PENDING" and row["attempts"] == 1 and "boom" in row["last_error"]
    assert row["run_at"] > utcnow()                                           # backed off into the future
    assert jobqueue.run_due() ["done"] == 0                                    # not due yet -> untouched
    execute("UPDATE job_queue SET run_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", jid))
    assert jobqueue.run_due()["retry"] == 1                                    # attempt 2 fails again
    execute("UPDATE job_queue SET run_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", jid))
    assert jobqueue.run_due()["done"] == 1                                     # attempt 3 succeeds
    assert query_one("SELECT status, attempts FROM job_queue WHERE id = ?", (jid,)) == {"status": "DONE", "attempts": 3}

    @jobqueue.handler("test.dead")
    def dead(payload):
        raise RuntimeError("always")
    j2 = jobqueue.enqueue("test.dead", {}, max_attempts=2)
    assert jobqueue.run_one(j2) == "RETRY"
    execute("UPDATE job_queue SET run_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (j2,))
    assert jobqueue.run_one(j2) == "FAILED"
    assert jobqueue.retry_failed(j2) is True and query_one("SELECT status FROM job_queue WHERE id = ?", (j2,))["status"] == "PENDING"
    _drain()


def test_queue_claim_is_exclusive_and_stale_locks_are_released():
    _drain()
    hits = []
    jobqueue.HANDLERS["test.once"] = lambda p: hits.append(1)
    jid = jobqueue.enqueue("test.once", {})
    ts = [threading.Thread(target=jobqueue.run_one, args=(jid,)) for _ in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(hits) == 1                                                      # five workers, one execution
    j2 = jobqueue.enqueue("test.once", {})
    execute("UPDATE job_queue SET status = 'RUNNING', locked_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (j2,))
    out = jobqueue.run_due()                                                   # crashed worker's job is recovered and run
    assert out["released"] >= 1 and query_one("SELECT status FROM job_queue WHERE id = ?", (j2,))["status"] == "DONE"
    assert jobqueue.run_one(99999999) is None
    _drain()


def test_unknown_job_kind_fails_visibly():
    _drain()
    jid = jobqueue.enqueue("test.nohandler", {}, max_attempts=1)
    assert jobqueue.run_one(jid) == "FAILED"
    assert "no handler" in query_one("SELECT last_error FROM job_queue WHERE id = ?", (jid,))["last_error"]
    _drain()


def test_webhook_failure_is_retried_until_it_succeeds(monkeypatch):
    state = {"status": 500, "hits": 0}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            state["hits"] += 1
            self.send_response(state["status"]); self.end_headers()
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(webhooks.netsafe, "validate_url", lambda u: u)         # loopback receiver for the test
    ws = query_one("SELECT workspace_id FROM workspace_members ORDER BY workspace_id LIMIT 1")["workspace_id"]
    execute("DELETE FROM webhooks WHERE workspace_id = ?", (ws,))      # exactly one hook, whatever ran before
    hid = execute("INSERT INTO webhooks(workspace_id, url, secret, events, is_active, created_at) VALUES(?,?,?,?,1,?)",
                  (ws, f"http://127.0.0.1:{srv.server_port}/", "s3cret", "ping,reply.received", utcnow()))
    execute("DELETE FROM job_queue WHERE kind = 'webhook'")
    try:
        with workspace_context(ws):
            assert webhooks.emit("reply.received", {"lead_id": 1}, wait=True) == 1
        job = query_one("SELECT * FROM job_queue WHERE kind = 'webhook' ORDER BY id DESC LIMIT 1")
        assert job["status"] == "PENDING" and job["attempts"] == 1 and "HTTP 500" in job["last_error"]
        state["status"] = 200                                                  # receiver recovers
        execute("UPDATE job_queue SET run_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (job["id"],))
        assert jobqueue.run_due()["done"] >= 1
        assert query_one("SELECT status FROM job_queue WHERE id = ?", (job["id"],))["status"] == "DONE"
        codes = [d["status_code"] for d in query("SELECT status_code FROM webhook_deliveries WHERE webhook_id = ? ORDER BY id", (hid,))]
        assert codes == [500, 200] and state["hits"] == 2
        # a hook deleted while a retry is pending is dropped, not retried forever
        with workspace_context(ws):
            state["status"] = 500
            webhooks.emit("reply.received", {"lead_id": 2}, wait=True)
        execute("DELETE FROM webhooks WHERE id = ?", (hid,))
        pend = query_one("SELECT id FROM job_queue WHERE kind = 'webhook' AND status = 'PENDING' ORDER BY id DESC LIMIT 1")
        execute("UPDATE job_queue SET run_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (pend["id"],))
        jobqueue.run_due()
        assert query_one("SELECT status FROM job_queue WHERE id = ?", (pend["id"],))["status"] == "DONE"
    finally:
        srv.shutdown()
        execute("DELETE FROM webhooks WHERE id = ?", (hid,))
        execute("DELETE FROM job_queue WHERE kind = 'webhook'")


def test_jobs_admin_endpoint(api):
    execute("DELETE FROM job_queue WHERE kind LIKE 'test.%'")
    jobqueue.HANDLERS["test.admin"] = lambda p: (_ for _ in ()).throw(RuntimeError("nope"))
    with workspace_context(WS):
        jid = jobqueue.enqueue("test.admin", {}, max_attempts=1)
    jobqueue.run_one(jid)
    listed = api.get("/api/jobs").json()
    assert listed["stats"].get("FAILED", 0) >= 1 and jid in [j["id"] for j in listed["jobs"]]
    assert api.post(f"/api/jobs/{jid}/retry").status_code == 200
    assert api.post(f"/api/jobs/{jid}/retry").status_code == 409          # no longer failed
    assert api.post("/api/jobs/99999999/retry").status_code == 404
    execute("DELETE FROM job_queue WHERE kind LIKE 'test.%'")


# ===================================================================== PDF ===
def _xref_ok(data: bytes) -> int:
    """Validate the cross-reference table byte offsets; returns the page count."""
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
    assert data[start:start + 4] == b"xref"
    n = int(re.search(rb"xref\n0 (\d+)\n", data).group(1))
    entries = re.findall(rb"(\d{10}) 00000 n ", data[start:])
    assert len(entries) == n - 1
    for number, off in enumerate(entries, 1):
        assert data[int(off):].startswith(b"%d 0 obj" % number), number
    return int(re.search(rb"/Count (\d+)", data).group(1))


def test_pdf_writer_structure_wrapping_and_escaping():
    from app import pdfsimple
    assert pdfsimple.width("iii", 10) < pdfsimple.width("WWW", 10)
    lines = pdfsimple.wrap("word " * 80, 200, 10)
    assert len(lines) > 3 and all(pdfsimple.width(l, 10) <= 200 for l in lines)
    assert all(pdfsimple.width(l, 10) <= 120 for l in pdfsimple.wrap("x" * 500, 120, 10))      # unbreakable token
    pdf = pdfsimple.Pdf()
    pdf.text(50, 800, "Paren (test) back\\slash ₹100 café 中", 12)
    pdf.new_page(); pdf.text(50, 800, "second", 12)
    data = pdf.render()
    assert _xref_ok(data) == 2
    assert b"\\(test\\)" in data and b"back\\\\slash" in data and b"Rs.100" in data


def _proposal(api, lid, n_items=3, **kw):
    items = [{"description": f"Line item number {i} " + "with a rather long description " * 4, "qty": i, "unit_price": 1234.5}
             for i in range(1, n_items + 1)]
    r = api.post("/api/proposals", json={"lead_id": lid, "title": "Big Website Rebuild & SEO (Phase 1)", "items": items,
                                         "tax_pct": 18, "discount_pct": 5, "notes": "Payment terms: 50% upfront.\n" * 3, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_proposal_pdf_endpoints(api):
    lid = _lead("Pdf Cafe")
    p = _proposal(api, lid)
    r = api.get(f"/api/proposals/{p['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert _xref_ok(r.content) == 1 and p["number"].encode() in r.content and b"Big Website Rebuild" in r.content
    anon = TestClient(app)
    assert anon.get(f"/p/{p['share_token']}/pdf").status_code == 404           # drafts are private
    assert anon.get(f"/api/proposals/{p['id']}/pdf").status_code == 401        # API needs a login
    api.post(f"/api/proposals/{p['id']}/send", json={})
    pub = anon.get(f"/p/{p['share_token']}/pdf")
    assert pub.status_code == 200 and pub.content.startswith(b"%PDF")
    assert "/pdf" in anon.get(f"/p/{p['share_token']}").text                   # the page links to it


def test_long_proposal_paginates(api):
    lid = _lead("Long Pdf Cafe")
    p = _proposal(api, lid, n_items=40)
    data = api.get(f"/api/proposals/{p['id']}/pdf").content
    pages = _xref_ok(data)
    assert pages >= 2
    assert f"Page {pages} of {pages}".encode() in data and b"Page 1 of" in data


# ================================================================== exports ===
def test_exports(api):
    lid = _lead("=cmd|evil Cafe")                                               # formula-injection attempt
    p = _proposal(api, lid)
    r = api.get("/api/proposals/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("number,title,lead,status") and "'=cmd|evil Cafe" in r.text
    assert api.get("/api/tasks/export").status_code == 200
    start = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds")
    for t in ("A call", "B call"):
        api.post("/api/meetings", json={"lead_id": lid, "title": t, "starts_at": start})
    cal = api.get("/api/meetings/calendar.ics")
    assert cal.status_code == 200 and cal.text.count("BEGIN:VEVENT") >= 2 and cal.text.count("BEGIN:VCALENDAR") == 1
    assert cal.text.count("END:VEVENT") == cal.text.count("BEGIN:VEVENT") and cal.text.endswith("END:VCALENDAR\r\n")
    assert "title" in api.get("/api/meetings/export").text.splitlines()[0]
