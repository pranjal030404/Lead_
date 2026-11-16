"""Campaign sequences: enrolment rules, step timing, reply/suppression stops, no double-mailing."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import campaigns, replies
from app.db import execute, query, query_one, utcnow, workspace_context
from app.main import app
from app.outreach import generate_followups, suppress
from app.security import SESSION_COOKIE, hash_password, issue_token

WS = None


@pytest.fixture()
def api():
    global WS
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token("admin"))
    WS = query_one("SELECT workspace_id FROM workspace_members ORDER BY workspace_id LIMIT 1")["workspace_id"]
    return c


def _lead(name, email="x@shop.example", status="NEW", valid=1, **extra):
    cols = {"workspace_id": WS, "business_name": name, "city": "Pune", "niche": "cafes", "lead_score": 6,
            "lead_priority": "WARM", "status": status, "email": email, "email_valid": valid,
            "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
                   tuple(cols.values()))


def _campaign(api, steps=None, **kw):
    steps = steps or [{"template": "cold_email_first", "delay_days": 0},
                      {"template": "follow_up_day_3", "delay_days": 2}]
    r = api.post("/api/campaigns", json={"name": "Test seq", "steps": steps, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _mark_sent(queue_id, days_ago=0):
    execute("UPDATE approval_queue SET status = 'SENT', sent_at = ? WHERE id = ?", (_ago(days_ago), queue_id))
    q = query_one("SELECT lead_id FROM approval_queue WHERE id = ?", (queue_id,))
    execute("UPDATE leads SET status = 'CONTACTED' WHERE id = ? AND status = 'NEW'", (q["lead_id"],))


def _enr(cid, lid):
    return query_one("SELECT * FROM campaign_enrollments WHERE campaign_id = ? AND lead_id = ?", (cid, lid))


def test_create_validates_templates(api):
    assert api.post("/api/campaigns", json={"name": "x", "steps": [{"template": "nope"}]}).status_code == 400
    wa = query_one("SELECT name FROM templates WHERE type != 'email' LIMIT 1")
    if wa:
        assert api.post("/api/campaigns", json={"name": "x", "steps": [{"template": wa["name"]}]}).status_code == 400
    assert api.post("/api/campaigns", json={"name": "x", "steps": []}).status_code == 422


def test_enrol_skip_reasons(api):
    c = _campaign(api)
    ok = _lead("Ok Cafe", "ok1@shop.example")
    noemail = _lead("No Email", None, valid=0)
    sup = _lead("Suppressed", "sup@shop.example")
    won = _lead("Already Won", "won@shop.example", status="WON")
    with workspace_context(WS):
        suppress("sup@shop.example", "email", "test")
    r = api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [ok, noemail, sup, won, ok, 99999999]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["enrolled"] == 1
    assert out["skipped"]["no email address"] == 1 and out["skipped"]["suppressed"] == 1
    assert out["skipped"]["lead is WON"] == 1 and out["skipped"]["not found"] == 1
    # a lead can only be in one active campaign
    c2 = _campaign(api)
    again = api.post(f"/api/campaigns/{c2['id']}/enroll", json={"lead_ids": [ok]}).json()
    assert again["enrolled"] == 0 and "already in an active campaign" in again["skipped"]


def test_full_sequence_timing(api):
    c = _campaign(api)
    lid = _lead("Seq Cafe", "seq@shop.example")
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [lid]})
    assert api.post(f"/api/campaigns/{c['id']}/run").status_code == 400        # still DRAFT
    assert api.patch(f"/api/campaigns/{c['id']}", json={"status": "ACTIVE"}).status_code == 200

    assert api.post(f"/api/campaigns/{c['id']}/run").json()["queued"] == 1      # step 0 -> approval queue
    q0 = query_one("SELECT * FROM approval_queue WHERE lead_id = ? AND step = ?", (lid, f"c{c['id']}s0"))
    assert q0 and q0["status"] == "PENDING"                                      # never sent automatically
    assert api.post(f"/api/campaigns/{c['id']}/run").json() == {"queued": 0, "stopped": 0, "completed": 0}

    _mark_sent(q0["id"], days_ago=0)                                             # sent just now
    api.post(f"/api/campaigns/{c['id']}/run")
    e = _enr(c["id"], lid)
    assert e["status"] == "ACTIVE" and e["waiting_queue_id"] is None and e["next_position"] == 1
    assert query_one("SELECT 1 AS x FROM approval_queue WHERE lead_id = ? AND step = ?", (lid, f"c{c['id']}s1")) is None

    execute("UPDATE approval_queue SET sent_at = ? WHERE id = ?", (_ago(3), q0["id"]))   # 3 days later
    execute("UPDATE campaign_enrollments SET next_due_at = ? WHERE id = ?", (_ago(1), e["id"]))
    assert api.post(f"/api/campaigns/{c['id']}/run").json()["queued"] == 1      # step 1 due now
    q1 = query_one("SELECT * FROM approval_queue WHERE lead_id = ? AND step = ?", (lid, f"c{c['id']}s1"))
    assert q1["status"] == "PENDING"

    _mark_sent(q1["id"])
    assert api.post(f"/api/campaigns/{c['id']}/run").json()["completed"] == 1
    assert _enr(c["id"], lid)["status"] == "COMPLETED"
    st = api.get(f"/api/campaigns/{c['id']}").json()["stats"]
    assert st["completed"] == 1 and st["sent"] == 2


def test_reply_stops_sequence(api):
    c = _campaign(api)
    lid = _lead("Reply Cafe", "reply@shop.example")
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [lid]})
    api.patch(f"/api/campaigns/{c['id']}", json={"status": "ACTIVE"})
    api.post(f"/api/campaigns/{c['id']}/run")
    r = api.post(f"/api/leads/{lid}/reply", json={"text": "Interested, tell me more"})
    assert r.status_code == 200
    e = _enr(c["id"], lid)
    assert e["status"] == "STOPPED" and e["stop_reason"].startswith("replied")
    assert query_one("SELECT status FROM approval_queue WHERE lead_id = ?", (lid,))["status"] == "SKIPPED"
    assert api.post(f"/api/campaigns/{c['id']}/run").json()["queued"] == 0
    assert api.get(f"/api/campaigns/{c['id']}").json()["stats"]["replied"] == 1


def test_auto_reply_does_not_stop(api):
    c = _campaign(api)
    lid = _lead("OOO Cafe", "ooo@shop.example")
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [lid]})
    api.post(f"/api/leads/{lid}/reply", json={"text": "I am out of office until Monday. Automatic reply."})
    assert _enr(c["id"], lid)["status"] == "ACTIVE"


def test_suppression_between_steps_stops(api):
    c = _campaign(api)
    lid = _lead("Late Opt Out", "late@shop.example")
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [lid]})
    api.patch(f"/api/campaigns/{c['id']}", json={"status": "ACTIVE"})
    with workspace_context(WS):
        suppress("late@shop.example", "email", "test")
    api.post(f"/api/campaigns/{c['id']}/run")
    e = _enr(c["id"], lid)
    assert e["status"] == "STOPPED" and "not eligible" in e["stop_reason"]


def test_paused_campaign_is_not_processed(api):
    c = _campaign(api)
    lid = _lead("Paused Cafe", "paused@shop.example")
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [lid]})
    api.patch(f"/api/campaigns/{c['id']}", json={"status": "ACTIVE"})
    api.patch(f"/api/campaigns/{c['id']}", json={"status": "PAUSED"})
    with workspace_context(WS):
        assert campaigns.tick() == {"queued": 0, "stopped": 0, "completed": 0}
    assert query_one("SELECT 1 AS x FROM approval_queue WHERE lead_id = ?", (lid,)) is None


def test_legacy_followups_skip_campaign_leads(api):
    c = _campaign(api)
    inside = _lead("In Campaign", "in@shop.example", status="CONTACTED", last_contacted_date=_ago(5))
    outside = _lead("Not In Campaign", "out@shop.example", status="CONTACTED", last_contacted_date=_ago(5))
    # enrol while NEW-eligible, then move to CONTACTED as a sent email would
    execute("UPDATE leads SET status = 'NEW' WHERE id = ?", (inside,))
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [inside]})
    execute("UPDATE leads SET status = 'CONTACTED' WHERE id = ?", (inside,))
    with workspace_context(WS):
        generate_followups()
    assert query_one("SELECT 1 AS x FROM approval_queue WHERE lead_id = ?", (inside,)) is None
    assert query_one("SELECT 1 AS x FROM approval_queue WHERE lead_id = ?", (outside,)) is not None


def test_enrol_by_criteria_and_delete_cancels_pending(api):
    c = _campaign(api, criteria={"niche": "cafes", "min_score": 6})
    lid = _lead("Criteria Cafe", "crit@shop.example", lead_score=7)
    low = _lead("Low Score Cafe", "low@shop.example", lead_score=2)
    out = api.post(f"/api/campaigns/{c['id']}/enroll", json={"use_criteria": True}).json()
    assert out["enrolled"] >= 1
    assert _enr(c["id"], lid) and _enr(c["id"], low) is None
    api.patch(f"/api/campaigns/{c['id']}", json={"status": "ACTIVE"})
    api.post(f"/api/campaigns/{c['id']}/run")
    assert api.delete(f"/api/campaigns/{c['id']}").status_code == 200
    assert query_one("SELECT status FROM approval_queue WHERE lead_id = ?", (lid,))["status"] == "SKIPPED"
    assert api.get(f"/api/campaigns/{c['id']}").status_code == 404


def test_other_users_cannot_see_campaign(api):
    c = _campaign(api)
    execute("DELETE FROM users WHERE username = 'camp_user'")
    uid = execute("INSERT INTO users(username, password_hash, role, is_active, created_at, updated_at) "
                  "VALUES('camp_user', ?, 'user', 1, ?, ?)", (hash_password("password123"), utcnow(), utcnow()))
    from app.tenancy import add_member
    add_member(WS, uid, "member")
    other = TestClient(app)
    other.cookies.set(SESSION_COOKIE, issue_token("camp_user"))
    assert other.get(f"/api/campaigns/{c['id']}").status_code == 404
    assert c["id"] not in [x["id"] for x in other.get("/api/campaigns").json()["campaigns"]]
    execute("DELETE FROM users WHERE id = ?", (uid,))


def test_repeat_reply_does_not_duplicate_open_task(api):
    lid = _lead("Dup Task Cafe", "duptask@shop.example")
    for _ in range(3):
        api.post(f"/api/leads/{lid}/reply", json={"text": "Yes interested, can we talk Tuesday?"})
    titles = [t["title"] for t in api.get("/api/tasks", params={"lead_id": lid}).json()["tasks"]]
    assert len(titles) == 1 and titles[0].startswith("Confirm a time")
    tid = api.get("/api/tasks", params={"lead_id": lid}).json()["tasks"][0]["id"]
    api.patch(f"/api/tasks/{tid}", json={"status": "DONE"})                       # once done, a new reply may add a fresh one
    api.post(f"/api/leads/{lid}/reply", json={"text": "Yes interested, can we talk Tuesday?"})
    assert len(api.get("/api/tasks", params={"lead_id": lid}).json()["tasks"]) == 1   # (the OPEN list: only the new one)


def test_ab_step_splits_leads_by_variant_and_reports(api):
    camp = _campaign(api, steps=[{"template": "cold_email_first", "template_b": "follow_up_day_3",
                                  "delay_days": 0}])
    assert camp["steps"][0]["template_b"] == "follow_up_day_3"
    a = _lead("AB even", email="even@shop.example")
    b = _lead("AB odd", email="odd@shop.example")
    even, odd = (a, b) if a % 2 == 0 else (b, a)
    api.post(f"/api/campaigns/{camp['id']}/enroll", json={"lead_ids": [even, odd]})
    api.patch(f"/api/campaigns/{camp['id']}", json={"status": "ACTIVE"})
    api.post(f"/api/campaigns/{camp['id']}/run")
    used = {r["lead_id"]: r["template_name"] for r in query(
        "SELECT lead_id, template_name FROM approval_queue WHERE step = ?", (f"c{camp['id']}s0",))}
    assert used[even] == "cold_email_first" and used[odd] == "follow_up_day_3"
    res = api.get(f"/api/campaigns/{camp['id']}").json()["stats"]["variants"]
    assert res[0]["A"]["template"] == "cold_email_first" and res[0]["B"]["template"] == "follow_up_day_3"


def test_ab_unknown_template_rejected(api):
    r = api.post("/api/campaigns", json={"name": "x", "steps": [
        {"template": "cold_email_first", "template_b": "nope_nope", "delay_days": 0}]})
    assert r.status_code == 400
